#!/usr/bin/env python3
"""Тесты линтера и очистки. Без зависимостей.

    python3 tests/test_lint.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "scripts")
FIXTURES = os.path.join(HERE, "fixtures")
sys.path.insert(0, SCRIPTS)
import lint  # noqa: E402
import sanitize  # noqa: E402

with open(lint.RULES_PATH, encoding="utf-8") as _fh:
    RULES = {rule["id"]: rule for rule in json.load(_fh)["rules"]}


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return fh.read()


def hit_rules(text, genre="article"):
    return {f["rule"] for f in lint.analyze(text, genre)["findings"]}


def hit_groups(text, genre="article"):
    return {RULES[r].get("group") for r in hit_rules(text, genre)} - {None}


def make_file(folder, name, text):
    """Создаёт файл внутри folder через tempfile и убеждается, что путь не вышел из folder."""
    handle, path = tempfile.mkstemp(prefix=name, suffix=".md", dir=folder)
    real = os.path.realpath(path)
    base = os.path.realpath(folder)
    if not real.startswith(base + os.sep):
        raise AssertionError("файл вне папки: {}".format(path))
    with os.fdopen(handle, "w", encoding="utf-8") as fh:
        fh.write(text)
    return real


class Sentences(unittest.TestCase):
    def test_abbreviations_and_initials_do_not_split(self):
        text = "В 2020 г. А. С. Пушкин (то есть не он) и др. потратили 5 тыс. руб. Далее шло т. е. другое."
        self.assertEqual(len(lint.split_sentences(text)), 2)

    def test_quotes_and_ellipsis_split(self):
        text = "«Пора», — сказал он… Дальше тишина! Правильно?"
        self.assertEqual(len(lint.split_sentences(text)), 3)

    def test_decimal_and_lowercase_do_not_split(self):
        text = "Выросло в 3.5 раза, а не упало. Верно."
        self.assertEqual(len(lint.split_sentences(text)), 2)


class Masking(unittest.TestCase):
    def test_code_urls_quotes_are_ignored(self):
        text = ("```python\nx = 'В современном мире важно отметить'\n```\n\n"
                "> Цитата: стоит отметить, что это не просто код.\n\n"
                "Ссылка https://example.com/?q=не+просто и `важно помнить` в строке.\n\n"
                "А здесь текст без маркеров.")
        self.assertEqual(hit_rules(text), set())

    def test_unclosed_code_fence_is_masked_to_the_end(self):
        self.assertEqual(hit_rules("Начало текста.\n\n```\nважно отметить, что"), set())


class Rules(unittest.TestCase):
    def test_strong_turns(self):
        for text in ("Это не просто инструмент. Это партнёр.", "Важно отметить, что время вышло.",
                     "X, такие как Y, полезен.", "Играет ключевую роль в развитии региона.",
                     "Это важный шаг на пути к автоматизации.", "Это идеальное решение для тех, кто ценит комфорт.",
                     "Незабываемый отдых вдохновляет насладиться гармонией.", "Однако, несмотря на успех, есть проблемы.",
                     "Что делает его незаменимым, так это цена.", "Подчёркивает важность регулярного тестирования.",
                     "В этой статье мы рассмотрим основные подходы.", "В заключение отметим главное."):
            with self.subTest(text=text):
                self.assertIn("turn-strong", hit_groups(text))

    def test_weak_turns_and_single_rules(self):
        self.assertIn("turn-weak", hit_groups("В современном мире всё меняется."))
        self.assertIn("P1", hit_rules("Шёл дождь. Однако, никто не ушёл."))
        self.assertNotIn("P1", hit_rules("Он, однако, согласился и однако остался."))
        self.assertIn("M1", hit_rules("- **Скорость:** быстро\n- **Цена:** дёшево"))
        self.assertIn("M2", hit_rules("Это **очень** важный текст с выделением."))
        self.assertIn("T3", hit_rules("Конечно! Вот пять способов повысить продуктивность."))
        self.assertIn("R4", hit_rules("Python является языком. Rust является языком. Go является языком. " * 12))
        self.assertIn("T2", hit_rules("Скрытый\u200bпробел в слове."))

    def test_trace_inside_link_is_caught(self):
        # URL маскируются, поэтому следы ищутся в исходнике, а не в «замаскированном» тексте.
        self.assertEqual(hit_rules("Источник: https://a.b/c?utm_source=chatgpt.com"), {"T1"})

    def test_folklore_markers_are_not_penalized(self):
        # В корпусе эти обороты у людей встречаются не реже, чем у моделей.
        text = "Не секрет, что дело не в лени. Тем не менее приступим. На самом деле всё проще."
        self.assertEqual(hit_rules(text), set())

    def test_genre_switches_rules(self):
        text = "## Заголовок\n\nТекст поста с решётками."
        self.assertIn("M4", hit_rules(text, "post"))
        self.assertNotIn("M4", hit_rules(text, "article"))
        flat = "Слова идут ровным потоком без частиц. " * 12
        self.assertIn("R5", hit_rules(flat, "post"))
        self.assertNotIn("R5", hit_rules(flat, "formal"))


class Fixtures(unittest.TestCase):
    def test_slop_fails(self):
        for name, genre in (("article_slop.md", "article"), ("post_slop.md", "post")):
            result = lint.analyze(fixture(name), genre)
            with self.subTest(fixture=name):
                self.assertFalse(result["passed"], result["score"])
                self.assertLess(result["score"], 40)
                self.assertTrue(result["map"])

    def test_clean_passes(self):
        for name, genre in (("article_clean.md", "article"), ("post_clean.md", "post")):
            result = lint.analyze(fixture(name), genre)
            with self.subTest(fixture=name):
                self.assertTrue(result["passed"], [f["rule"] for f in result["findings"]])
                self.assertGreaterEqual(result["score"], lint.analyze("", "article")["gate"])

    def test_before_after_report(self):
        before = lint.analyze(fixture("article_slop.md"), "article")
        after = lint.analyze(fixture("article_clean.md"), "article")
        self.assertGreater(after["score"], before["score"])
        self.assertIn("БЫЛО → СТАЛО", lint.render(dict(after, before=before)))


class Sanitize(unittest.TestCase):
    def test_removes_invisible_keeps_emoji(self):
        text = "сло\u200bво\u2060и 👨\u200d👩\u00ad"
        cleaned, found = sanitize.clean(text)
        self.assertEqual(cleaned, "словои 👨\u200d👩")
        # Засчитаны пробел нулевой ширины, соединитель слов и мягкий перенос; соединитель внутри эмодзи оставлен.
        self.assertEqual(len(found), 3)
        # Внутри слова пробел нулевой ширины склеивается, между словами и знаками становится пробелом.
        self.assertEqual(sanitize.clean("сло\u200bво.\u200bДругое")[0], "слово. Другое")

    def test_cli_reports_and_prints_cleaned_text_without_touching_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = make_file(tmp, "draft", "Слово.\u200bВторой\u2060цикл.")
            relative = os.path.relpath(path, os.path.realpath(tmp))

            def run(*args):
                return subprocess.run([sys.executable, os.path.join(SCRIPTS, "sanitize.py"), *args],
                                      capture_output=True, text=True, encoding="utf-8", cwd=tmp)

            report_only = run(relative)
            self.assertEqual(report_only.returncode, 1)
            self.assertIn("U+200B", report_only.stdout)
            self.assertNotIn("Второй", report_only.stdout)
            fixed = run(relative, "--fix")
            self.assertEqual(fixed.returncode, 0)
            self.assertEqual(fixed.stdout, "Слово. Второйцикл.")
            self.assertEqual(fixed.stderr.strip().splitlines()[0], "Невидимые символы:")
            with open(path, encoding="utf-8") as fh:
                # Скрипт ничего не записывает: исходный файл не изменился.
                self.assertEqual(fh.read(), "Слово.\u200bВторой\u2060цикл.")

    def test_cli_refuses_to_read_outside_root(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            target = make_file(outside, "x", "Текст.")
            run = subprocess.run([sys.executable, os.path.join(SCRIPTS, "sanitize.py"), target],
                                 capture_output=True, text=True, encoding="utf-8", cwd=tmp)
            self.assertEqual(run.returncode, 2)
            self.assertIn("вне рабочей папки", run.stderr)


class Paths(unittest.TestCase):
    def test_linter_refuses_paths_outside_root(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            target = make_file(outside, "x", "Текст.")

            def run(*args):
                return subprocess.run([sys.executable, os.path.join(SCRIPTS, "lint.py"), *args],
                                      capture_output=True, text=True, encoding="utf-8", cwd=tmp)

            # Путь через родительский каталог и абсолютный путь вне рабочей папки отклоняются.
            self.assertEqual(run(os.path.join(os.pardir, os.path.basename(outside), os.path.basename(target))).returncode, 2)
            self.assertEqual(run(target).returncode, 2)
            allowed = run(target, "--root", outside, "--json")
            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            self.assertEqual(json.loads(allowed.stdout)["genre"], "article")
            from_stdin = subprocess.run([sys.executable, os.path.join(SCRIPTS, "lint.py"), "-", "--genre", "post"],
                                        input="Пост из stdin.", capture_output=True, text=True, encoding="utf-8", cwd=tmp)
            self.assertEqual(from_stdin.returncode, 0, from_stdin.stderr)


class Spec(unittest.TestCase):
    def test_rules_file_is_consistent(self):
        with open(lint.RULES_PATH, encoding="utf-8") as fh:
            spec = json.load(fh)
        ids = [rule["id"] for rule in spec["rules"]]
        self.assertEqual(len(ids), len(set(ids)))
        for rule in spec["rules"]:
            for pattern in rule.get("patterns", []):
                lint.re.compile(pattern)
            for band in rule.get("bands", []):
                key = "below" if "below" in band else "above" if "above" in band else None
                if key:
                    self.assertIn(band[key], lint.PERCENTILE)
                    for genre in lint.GENRES:
                        self.assertIn(rule["metric"], spec["reference"][genre])
        for genre in lint.GENRES:
            for row in spec["map"]:
                self.assertEqual(len(spec["reference"][genre][row["key"]]), 7, (genre, row["key"]))


if __name__ == "__main__":
    unittest.main()
