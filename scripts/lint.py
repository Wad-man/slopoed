#!/usr/bin/env python3
"""slopoed: линтер русского ИИ-слопа. Без зависимостей, Python 3.9+.

Находит в тексте следы нейросетевой манеры и печатает карту текста: где он
стоит рядом с человеческой нормой своего жанра, а где от неё отошёл.

    python3 lint.py черновик.md
    python3 lint.py черновик.md --genre post
    python3 lint.py черновик.md --before исходник.md
    python3 lint.py - --json < черновик.md

Балл — это чистота по правилам линтера, а не вероятность авторства. Линтер
пол, а не вердикт: всё, что он нашёл, реально; всё, что пропустил, не обязано
быть чистым.

Коды возврата: 0 — прошёл порог, 1 — не прошёл, 2 — ошибка использования.
"""
import argparse
import bisect
import json
import os
import re
import statistics
import sys

RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules.json")
GENRES = ("post", "article", "formal")
GENRE_NAME = {"post": "пост, письмо, отзыв", "article": "статья, лонгрид, блог", "formal": "новости, справка, документ"}
SEVERITY_ORDER = ("note", "minor", "major", "blocker")
SEVERITY_NAME = {"note": "заметка", "minor": "небольшая", "major": "серьёзная", "blocker": "блокирует"}
GROUP_NAME = {"turn-strong": "сильные обороты", "turn-weak": "слабые обороты"}
PERCENTILE = {"p5": 0, "p10": 1, "p25": 2, "p50": 3, "p75": 4, "p90": 5, "p95": 6}
# Замеры, которые есть только у структурных правил и не печатаются в карте.
METRIC_INFO = {"traits": {"name": "Живые приметы", "kind": "int"}}

WORD = re.compile(r"[A-Za-zА-ЯЁа-яё0-9]+(?:-[A-Za-zА-ЯЁа-яё0-9]+)*")
NOM = re.compile(r"\b[а-яё]{4,}(?:ация|ение|ание)\b", re.I)
PARTICLE = re.compile(r"\b(?:же|ведь|вот|ли|ну|уж|типа|мол|дескать|якобы|короче|кстати|вообще|блин)\b|\w+-то\b", re.I)
INITCONJ = re.compile(r"(?:(?<=[.!?…])|^)\s*(?:И|Но|А)\s+[а-яё]", re.M)
NUMBER = re.compile(
    r"\b\d+[\d\s.,]*\d\b|\b\d\b|"
    r"\b(?:од(?:ин|на|но|ни|ного|ной|ному|ним|ной|них|ними|на|ну)|дв(?:а|е|у|умя|ух|умя|ое)|три|тр[её]х|четыр[её]|четыр[её]х|"
    r"пять|пят[ьи]|шест[ьои]|сем[ььи]|восем[ььи]|девят[ьои]|десят[ььи]|одиннадцат[ььи]|двенадцат[ььи]|тринадцат[ььи]|"
    r"четырнадцат[ььи]|пятнадцат[ььи]|шестнадцат[ььи]|семнадцат[ььи]|восемнадцат[ььи]|девятнадцат[ььи]|"
    r"двадцат\w*|тридцат\w*|сорок|пят\w*десят\w*|шест\w*десят\w*|сем\w*десят\w*|восем\w*десят\w*|девя\w*десят\w*|"
    r"сто|ст[ао]их|сот\w*|тысяч\w*|миллион\w*|миллиард\w*|полтор\w*|полсотни|половин\w*)\b", re.I)
JAVLJ = re.compile(r"\bявля(?:ет|ют)ся\b", re.I)
FIRSTPERSON = re.compile(r"\b(?:я|мне|меня|мо[йяеё]|мои|мы|нам|нас|наш|наша|наше|наши)\b", re.I)
ELLIPSIS = re.compile("…|\\.\\.\\.")
NEGATION = re.compile(r"\bне\s", re.I)
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐-⯿]")

# Не набираются с клавиатуры: в тексте из чата встречаются, в живом почти нет.
INVISIBLE = set(
    list(range(0x200B, 0x200F)) + list(range(0x202A, 0x202E)) + list(range(0x2060, 0x2065))
    + list(range(0x2066, 0x206A)) + list(range(0xE0000, 0xE0080)) + list(range(0xFDD0, 0xFDF0))
    + list(range(0xFFF0, 0xFFF9)) + [0x00AD, 0x061C, 0x180E, 0x202F, 0x3164, 0xFEFF, 0xFFA0])

ABBREV = set(
    "т е п д др пр ул г рис табл гл стр ст им ред проф акад доц ген мл вн экс напр см п подп разд ок кв пер пл "
    "бульв наб корп оф эт н м л с ч тел дом пос исп англ нем фр лат греч прил".split())
BOUND = re.compile(r"(?<=[.!?…])[\"»')\]]*\s+(?=[\"«('(\[]*[A-ZА-ЯЁ0-9])")
HEADING = re.compile(r"^\s{0,3}#{1,6}\s")
LIST = re.compile(r"^\s{0,3}(?:[-*+]|\d+[.)])\s+")
TABLE = re.compile(r"^\s*\|")


class UsageError(Exception):
    """Неверный вызов: вызывающему нужно сообщение, а не трассировка."""


# --- Пути: читаем только внутри разрешённых папок ---------------------------

def norm(path):
    return os.path.normcase(os.path.realpath(path))


def resolve_inside(raw, roots):
    """Нормализует путь и пропускает только тот, что лежит в разрешённых папках."""
    if ".." in raw.replace("\\", "/").split("/"):
        raise UsageError("в пути есть «..»: {}".format(raw))
    full = norm(raw)
    for root in roots:
        if full == root or full.startswith(root + os.sep):
            return os.path.realpath(raw)
    raise UsageError(
        "файл вне рабочей папки: {}. Передай --root ПАПКА или подай текст на stdin.".format(raw))


def read_text(path, roots):
    if path == "-":
        return sys.stdin.read()
    full = resolve_inside(path, roots)
    if not os.path.isfile(full):
        raise UsageError("нет такого файла: {}".format(path))
    with open(full, encoding="utf-8-sig") as fh:
        return fh.read()


# --- Маскировка: не проверяем то, что написано не как проза -----------------

def mask_regions(text):
    """Заменяет код, URL, блоки цитат и комментарии пробелами, сохраняя строки."""
    chars = list(text)

    def blank(a, b):
        for i in range(a, min(b, len(chars))):
            if chars[i] != "\n":
                chars[i] = " "

    if text.startswith("---"):
        m = re.match(r"\A---\n.*?\n---\n", text, re.S)
        if m:
            blank(m.start(), m.end())
    for pattern, flags in (
            (r"^```.*?(?:^```|\Z)", re.S | re.M),
            (r"^~~~.*?(?:^~~~|\Z)", re.S | re.M),
            (r"<!--.*?-->", re.S),
            (r"`[^`\n]+`", 0),
            (r"https?://[^\s<>()]+", 0),
            (r"\]\([^)\s]+\)", 0),
            (r"^\s*>.*$", re.M),
    ):
        for m in re.finditer(pattern, text, flags):
            blank(m.start(), m.end())
    return "".join(chars)


# --- Блоки и предложения ----------------------------------------------------

def prose_blocks(masked):
    """Режет текст на абзацы и пункты списков: [(вид, текст, смещение)].

    Заголовки и строки таблиц в статистику не идут: это не предложения.
    """
    blocks = []
    current = None
    offset = 0
    for chunk in re.findall(r"[^\n]*\n?", masked):
        pos = offset
        offset += len(chunk)
        body = chunk.strip()
        if not body:
            current = None
            continue
        if HEADING.match(chunk) or TABLE.match(chunk):
            current = None
            continue
        m = LIST.match(chunk)
        if m:
            blocks.append(["list", chunk[m.end():].strip(), pos + m.end()])
            current = len(blocks) - 1
            continue
        if current is None or blocks[current][0] == "list":
            blocks.append(["prose", body, pos])
            current = len(blocks) - 1
        else:
            blocks[current][1] += " " + body
    return [tuple(b) for b in blocks if b[1]]


def split_sentences(text):
    """Режет русский текст на предложения, не путая аббревиатуры и инициалы."""
    out = []
    start = 0
    for m in BOUND.finditer(text):
        before = text[max(0, m.start() - 8):m.start()]
        tail = re.search(r"([A-Za-zА-ЯЁа-яё0-9]*)[.!?…]+$", before)
        token = tail.group(1) if tail else ""
        # Единицы и валюты («руб.», «тыс.») в список не входят: перед заглавной буквой
        # точка после них чаще заканчивает предложение, чем открывает новое слово.
        if (len(token) == 1 and token.isupper()) or token in ABBREV:
            continue
        if re.search(r"[A-Za-zА-ЯЁа-яё]{2,}", text[start:m.start()]):
            out.append((start, m.start()))
        start = m.end()
    if re.search(r"[A-Za-zА-ЯЁа-яё]{2,}", text[start:]):
        out.append((start, len(text)))
    return out


def longest_run(lens, tolerance):
    """Самый длинный ряд соседних предложений с разницей длин не больше tolerance."""
    best, at, cur = 1, 0, 1
    for i in range(1, len(lens)):
        prev, cur_len = lens[i - 1], lens[i]
        if prev and abs(cur_len - prev) <= tolerance * max(prev, cur_len):
            cur += 1
            if cur > best:
                best, at = cur, i - cur + 1
        else:
            cur = 1
    return best, at


# --- Замеры ----------------------------------------------------------------

def measure(masked):
    """Карта текста. Ключи совпадают с map и reference в rules.json."""
    blocks = prose_blocks(masked)
    sent_at, lens = [], []
    for _, text, off in blocks:
        for a, b in split_sentences(text):
            n = len(WORD.findall(text[a:b]))
            if n:
                sent_at.append(off + a)
                lens.append(n)
    body = " ".join(text for _, text, _ in blocks)
    words = WORD.findall(body)
    nw, nc = max(len(words), 1), max(len(body), 1)
    m = {"words": len(words), "sentences": len(lens), "lens": lens, "sent_at": sent_at}
    m["mean_len"] = statistics.mean(lens) if lens else 0.0
    m["len_cv"] = statistics.pstdev(lens) / m["mean_len"] if len(lens) > 1 and m["mean_len"] else 0.0
    m["short4"] = 100.0 * sum(1 for x in lens if x <= 4) / len(lens) if lens else 0.0
    m["long25"] = 100.0 * sum(1 for x in lens if x >= 25) / len(lens) if lens else 0.0
    m["run"], m["run_at"] = longest_run(lens, 0.25)
    m["word_len"] = statistics.mean(len(w) for w in words) if words else 0.0
    m["nom100"] = 100.0 * len(NOM.findall(body)) / nw
    m["particles"] = 1000.0 * len(PARTICLE.findall(body)) / nw
    m["initconj"] = 1000.0 * len(INITCONJ.findall(body)) / nw
    m["parens"] = 1000.0 * body.count("(") / nw
    m["questions"] = 1000.0 * body.count("?") / nw
    m["ellipsis"] = 1000.0 * len(ELLIPSIS.findall(body)) / nw
    m["negation"] = 1000.0 * len(NEGATION.findall(body)) / nw
    m["numbers"] = 1000.0 * len(NUMBER.findall(body)) / nw
    m["javlj_n"] = len(JAVLJ.findall(body))
    m["javlj"] = 1000.0 * m["javlj_n"] / nw
    m["firstperson"] = 1000.0 * len(FIRSTPERSON.findall(body)) / nw
    m["emdash"] = 1000.0 * body.count("—") / nc
    # Живые приметы: то, что человек пишет по привычке, а модель — редко.
    m["traits"] = sum(1 for k in ("particles", "initconj", "parens", "questions", "ellipsis",
                                  "firstperson", "short4", "emdash") if m[k] > 0)
    return m


# --- Правила ----------------------------------------------------------------

def active_rules(spec, genre):
    out = []
    for rule in spec["rules"]:
        if "only" in rule and genre not in rule["only"]:
            continue
        if "mute" in rule and genre in rule["mute"]:
            continue
        out.append(rule)
    return out


def flags(rule):
    return re.M | (0 if rule.get("case_sensitive") else re.I)


def locate(starts, pos):
    i = max(bisect.bisect_right(starts, pos) - 1, 0)
    return i + 1, pos - starts[i] + 1


def finding(rule, pos, match, starts, severity=None, extra=None):
    line, col = locate(starts, pos)
    f = {"rule": rule["id"], "name": rule["name"], "severity": severity or rule.get("severity", "note"),
         "group": rule.get("group"), "line": line, "col": col,
         "match": " ".join(match.split())[:90], "fix": rule.get("fix", "")}
    if extra:
        f.update(extra)
    return f


def band_matched(band, value, ref, metric):
    """Полоса — это «ниже такого персентиля человека» или «выше»."""
    if "below" in band:
        return value < ref[metric][PERCENTILE[band["below"]]]
    if "above" in band:
        return metric in ref and value > ref[metric][PERCENTILE[band["above"]]]
    if "equals" in band:
        return value == band["equals"]
    if "at_least" in band:
        return value >= band["at_least"]
    return value <= band["at_most"] if "at_most" in band else False


def structure_hit(rule, m, spec, ref):
    """Самая строгая полоса, в которую попал замер: (severity, value) или None.

    Пороги полос берутся из нормы человека по жанру, поэтому одно и то же
    правило в посте и в документе срабатывает на разном.
    """
    metric = rule.get("metric")
    if metric not in m:
        return None
    if m.get("words", 0) < rule.get("min_words", 60) or m.get("sentences", 0) < rule.get("min_sent", 4):
        return None
    if "min_count" in rule and m.get(metric + "_n", 0) < rule["min_count"]:
        return None
    value = m[metric]
    matched = [band for band in rule["bands"] if band_matched(band, value, ref, metric)]
    if not matched:
        return None
    band = matched[-1]
    return band["severity"], value


def structure_findings(rule, starts, m, spec, ref):
    hit = structure_hit(rule, m, spec, ref)
    if not hit:
        return []
    severity, value = hit
    metric = rule.get("metric")
    info = METRIC_INFO.get(metric) or next(r for r in spec["map"] if r["key"] == metric)
    text = info["name"].lower()
    if not any(k in rule["bands"][-1] for k in ("equals", "at_most")):
        text += ": " + fmt(value, info["kind"])
    return [finding(rule, (m["sent_at"] or [0])[0], text, starts, severity=severity, extra={"value": round(value, 2)})]


def check_rules(rules, masked, raw, starts, m, chars, spec, ref):
    """Правило с «raw» проверяет исходник целиком: след в ссылке или в коде тоже след."""
    findings = []
    for rule in rules:
        kind = rule.get("kind", "regex")
        if kind == "structure":
            findings += structure_findings(rule, starts, m, spec, ref)
        elif kind == "density":
            hits = sorted(x.start() for pattern in rule["patterns"] for x in re.finditer(pattern, masked, flags(rule)))
            base = m["words"] if rule["per"] == "words" else chars
            rate = rule["scale"] * len(hits) / max(base, 1)
            if len(hits) >= rule["min_count"] and rate > rule["above"]:
                text = "{} срабатываний, {} на {} {}".format(
                    len(hits), fmt(rate, "num1"), rule["scale"], "слов" if rule["per"] == "words" else "знаков")
                findings.append(finding(rule, hits[0], text, starts, extra={"count": len(hits), "rate": round(rate, 2)}))
        else:
            target = raw if rule.get("raw") else masked
            for pattern in rule["patterns"]:
                for x in re.finditer(pattern, target, flags(rule)):
                    findings.append(finding(rule, x.start(), x.group(0), starts))
    return findings


def invisible_findings(text, starts):
    out, seen = [], {}
    for i, ch in enumerate(text):
        if ord(ch) in INVISIBLE:
            seen.setdefault(ord(ch), []).append(locate(starts, i)[0])
    for cp, lines in sorted(seen.items()):
        rule = {"id": "T2", "name": "Невидимый символ", "severity": "major",
                "fix": "Не виден, но ломает поиск и копипасту. Прогон sanitize.py --fix."}
        out.append(finding(rule, 0, "U+{:04X} в строках {}".format(cp, ", ".join(str(x) for x in lines[:8])), starts))
    return out


def points_for(rule, severity, cfg):
    override = rule.get("points", {})
    return override[severity] if severity in override else cfg["points"][severity]


def score(findings, spec):
    cfg = spec["scoring"]
    rules = {r["id"]: r for r in spec["rules"]}
    groups, by_rule = {}, {}
    for f in findings:
        if f["group"]:
            groups.setdefault(f["group"], set()).add(f["rule"])
        else:
            by_rule.setdefault(f["rule"], []).append(f)
    total, breakdown = 0.0, []
    for gid in sorted(groups):
        g = cfg["groups"][gid]
        value = min(g["first"] + (len(groups[gid]) - 1) * g["next"], g["cap"])
        total += value
        breakdown.append({"rule": gid, "name": GROUP_NAME.get(gid, gid), "count": len(groups[gid]),
                          "items": sorted(groups[gid]), "deduction": round(value, 1)})
    for rid in sorted(by_rule):
        fs = by_rule[rid]
        rule = rules.get(rid, {})
        sev = fs[0]["severity"]
        base = points_for(rule, sev, cfg)
        value = min(base + (len(fs) - 1) * cfg["repeat"][sev], base * cfg["cap_mult"]) if base else 0.0
        total += value
        breakdown.append({"rule": rid, "name": fs[0]["name"], "count": len(fs), "deduction": round(value, 1)})
    return max(0, round(cfg["start"] - total)), breakdown


def map_rows(spec, m, genre):
    ref = spec["reference"][genre]
    rows = []
    for row in spec["map"]:
        key = row["key"]
        if key not in m or key not in ref:
            continue
        value, norm = m[key], ref[key]
        lo, mid, hi = norm[1], norm[3], norm[5]
        concern = row["concern"]
        if concern == "genre" and genre != "post":
            concern = "none"
        flag = ""
        if concern == "low" and (value < norm[2] or (value == 0 and mid >= 5)):
            flag = "ниже нормы"
        elif concern == "high" and value > norm[4]:
            flag = "выше нормы"
        rows.append({"key": key, "name": row["name"], "kind": row["kind"], "value": round(value, 3),
                     "lo": lo, "mid": mid, "hi": hi, "flag": flag})
    return rows


def fmt(x, kind="num2"):
    if kind == "int":
        return str(int(round(x)))
    return "{:.{d}f}".format(x, d=1 if kind == "num1" else 2).replace(".", ",")


def analyze(text, genre="article", rules_path=RULES_PATH, gate=None):
    if genre not in GENRES:
        raise UsageError("жанр должен быть одним из: {}".format(", ".join(GENRES)))
    with open(rules_path, encoding="utf-8") as fh:
        spec = json.load(fh)
    masked = mask_regions(text)
    starts = [0] + [x.end() for x in re.finditer("\n", text)]
    m = measure(masked)
    ref = spec["reference"][genre]
    findings = invisible_findings(text, starts)
    findings += check_rules(active_rules(spec, genre), masked, text, starts, m, len(masked), spec, ref)
    value, breakdown = score(findings, spec)
    threshold = spec["scoring"]["gate"] if gate is None else gate
    enough = m["words"] >= 60 and m["sentences"] >= 4
    return {
        "score": value,
        "gate": threshold,
        "passed": value >= threshold and not any(f["severity"] == "blocker" for f in findings),
        "genre": genre,
        "words": m["words"],
        "sentences": m["sentences"],
        "findings": findings,
        "breakdown": breakdown,
        "map": map_rows(spec, m, genre) if enough else [],
        "map_note": "" if enough else "Карта не считается: нужно минимум 60 слов и 4 предложения.",
    }


# --- Вывод ------------------------------------------------------------------

def render(result, show_fixes=True):
    out = ["slopoed · чистота {}/100 · {} · жанр: {} · слов: {}, предложений: {}".format(
        result["score"], "проходит порог" if result["passed"] else "НЕ ПРОХОДИТ (порог {})".format(result["gate"]),
        GENRE_NAME[result["genre"]], result["words"], result["sentences"])]
    if result["map_note"]:
        out.append(result["map_note"])
    if not result["findings"]:
        out += ["", "Находок нет. Это пол, а не вердикт: линтер не читает смысл и не отличает",
                "выдуманное число от настоящего."]
    else:
        out += render_findings(result["findings"], show_fixes)
        out += render_breakdown(result["breakdown"])
    if result["map"]:
        out += render_map(result["map"])
    if result.get("before"):
        out += render_before(result["before"], result)
    return "\n".join(out)


def render_findings(findings, show_fixes):
    rank = {s: i for i, s in enumerate(SEVERITY_ORDER)}
    by_rule = {}
    for f in findings:
        by_rule.setdefault(f["rule"], []).append(f)
    ordered = sorted(by_rule.values(), key=lambda fs: (-max(rank[x["severity"]] for x in fs), fs[0]["rule"]))
    out = ["", "НАХОДКИ ({} правил)".format(len(ordered))]
    for fs in ordered:
        top = max(rank[x["severity"]] for x in fs)
        out.append("")
        out.append("  [{}] {} · {}{}".format(fs[0]["rule"], fs[0]["name"], SEVERITY_NAME[SEVERITY_ORDER[top]],
                                            "" if len(fs) == 1 else " · ×{}".format(len(fs))))
        for f in fs[:3]:
            out.append("    {}: {}  «{}»".format(f["line"], f["col"], f["match"]))
        if len(fs) > 3:
            out.append("    ещё в строках: " + ", ".join(str(f["line"]) for f in fs[3:11])
                       + (", …" if len(fs) > 11 else ""))
        if show_fixes and fs[0]["fix"]:
            out.append("    → {}".format(fs[0]["fix"]))
    return out


def render_breakdown(breakdown):
    if not breakdown:
        return []
    parts = []
    for b in breakdown:
        what = "{} ({})".format(b["name"], ", ".join(b["items"])) if "items" in b else "{} ×{}".format(b["name"], b["count"])
        parts.append("{}: {}".format(what, fmt(b["deduction"], "num1")))
    return ["", "ШТРАФЫ: " + "; ".join(parts)]


def render_map(rows):
    out = ["", "КАРТА ТЕКСТА                                   текст      человек: 10% · медиана · 90%"]
    for r in rows:
        out.append("  {:<38} {:>6}       {:>6} · {:>6} · {:>6}   {}".format(
            r["name"][:38], fmt(r["value"], r["kind"]), fmt(r["lo"], r["kind"]), fmt(r["mid"], r["kind"]),
            fmt(r["hi"], r["kind"]), "← " + r["flag"] if r["flag"] else ""))
    out += ["  Норма — живые русские тексты корпуса LLMTrace для этого жанра.",
            "  «Ниже» и «выше нормы»: вышли за 25-й или 75-й персантиль человека."]
    return out


def render_before(before, after):
    out = ["", "БЫЛО → СТАЛО: чистота {} → {}".format(before["score"], after["score"])]
    old = {r["key"]: r["value"] for r in before["map"]}
    for r in after["map"]:
        if r["key"] in old and abs(old[r["key"]] - r["value"]) >= 0.05:
            out.append("  {:<34} {} → {}".format(r["name"][:34], fmt(old[r["key"]], r["kind"]), fmt(r["value"], r["kind"])))
    return out


def setup_stdout():
    # В пайп и файл пишем UTF-8, на консоли оставляем её кодировку. Символы,
    # которые консоль не умеет, заменяем вместо падения.
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(errors="replace")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Линтер русского ИИ-слопа.")
    parser.add_argument("path", help="Файл с текстом или - для stdin.")
    parser.add_argument("--genre", choices=GENRES, default="article", help="Жанр текста (по умолчанию article).")
    parser.add_argument("--gate", type=int, default=None, help="Порог чистоты (по умолчанию из rules.json).")
    parser.add_argument("--before", metavar="FILE", help="Исходник, с которым сравнить чистоту.")
    parser.add_argument("--root", action="append", default=[], metavar="DIR",
                        help="Дополнительная разрешённая папка (по умолчанию только текущая).")
    parser.add_argument("--rules", default=RULES_PATH, help="Свой файл правил.")
    parser.add_argument("--json", action="store_true", help="Вывод в JSON.")
    parser.add_argument("--quiet", action="store_true", help="Без подсказок, что исправлять.")
    args = parser.parse_args(argv)
    try:
        setup_stdout()
        roots = [norm(os.getcwd())] + [norm(r) for r in args.root]
        rules_path = resolve_inside(args.rules, roots + [norm(os.path.dirname(RULES_PATH))])
        result = analyze(read_text(args.path, roots), args.genre, rules_path, args.gate)
        if args.before:
            result["before"] = analyze(read_text(args.before, roots), args.genre, rules_path, args.gate)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(render(result, show_fixes=not args.quiet))
        return 0 if result["passed"] else 1
    except UsageError as exc:
        sys.stderr.write("Ошибка: {}\n".format(exc))
        return 2
    except (OSError, ValueError) as exc:
        sys.stderr.write("Ошибка: {}\n".format(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
