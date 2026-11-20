import ast
import os
import re
import string
import unittest
from pathlib import Path
from unittest import mock

from beamcam import i18n

SOURCE = Path(__file__).resolve().parent.parent / "src" / "beamcam"
LOCALE = SOURCE / "locale"


def marked_strings() -> set[str]:
    """Every literal passed to _() or N_() in the app."""
    found = set()
    for path in SOURCE.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ("_", "N_")
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                found.add(node.args[0].value)
    return found


def fields(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


class CatalogTest(unittest.TestCase):
    def test_every_text_is_translated_with_the_same_placeholders(self):
        texts = marked_strings()
        self.assertGreater(len(texts), 50)
        for po in LOCALE.glob("*.po"):
            catalog = i18n.parse_po(po.read_text(encoding="utf-8"))
            with self.subTest(language=po.stem):
                self.assertEqual(sorted(texts - catalog.keys()), [], "untranslated")
                self.assertEqual(sorted(catalog.keys() - texts), [], "no longer used")
                for source, translated in catalog.items():
                    self.assertEqual(fields(source), fields(translated), source)
                    self.assertEqual(re.findall(r"</?b>", source), re.findall(r"</?b>", translated), source)

    def test_every_catalog_is_offered(self):
        self.assertEqual({p.stem for p in LOCALE.glob("*.po")} | {"en"}, set(i18n.LANGUAGES))

    def test_helper_messages_match_the_helper(self):
        from beamcam import virtualcam

        helper = (SOURCE / "data" / "setup-virtual-camera").read_text()
        for message in virtualcam.HELPER_MESSAGES:
            self.assertIn(f'echo "{message}" >&2', helper)


class ParsePoTest(unittest.TestCase):
    def test_multiline_escapes_and_fuzzy(self):
        po = '\n'.join([
            'msgid ""', 'msgstr ""', '"Language: de\\n"', '',
            'msgid "One"', 'msgstr "Eins"', '',
            '#, fuzzy', 'msgid "Two"', 'msgstr "Zwei?"', '',
            'msgid "Three"', 'msgstr "Drei"', '',
            'msgid ""', '"Long "', '"text"', 'msgstr "Langer "', '"Text mit \\"Zitat\\""', '',
            'msgid "Untranslated"', 'msgstr ""',
        ])
        self.assertEqual(i18n.parse_po(po), {"One": "Eins", "Three": "Drei", "Long text": 'Langer Text mit "Zitat"'})


class LanguageTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(i18n.setup, "en")

    def test_system_language_from_environment(self):
        os.environ["LANG"] = "de_DE.UTF-8"
        self.assertEqual(i18n.system_language(), "de")
        os.environ["LANGUAGE"] = "fr:en"
        self.assertEqual(i18n.system_language(), "en")
        os.environ.clear()
        os.environ["LANG"] = "fr_FR.UTF-8"
        self.assertEqual(i18n.system_language(), "en")

    def test_chosen_language_wins_and_reaches_gtk(self):
        os.environ["LANG"] = "en_US.UTF-8"
        self.assertEqual(i18n.setup("de"), "de")
        self.assertEqual(os.environ["LANGUAGE"], "de")
        self.assertEqual(i18n._("Connect"), "Verbinden")
        self.assertEqual(i18n._("Not in the catalog"), "Not in the catalog")

    def test_back_to_the_system_language_after_a_restart(self):
        os.environ["LANG"] = "de_DE.UTF-8"
        os.environ["LANGUAGE"] = "de:en"
        i18n.setup("en")
        self.assertEqual(os.environ["LANGUAGE"], "en")
        self.assertEqual(i18n.system_language(), "de")
        # The restart keeps the environment; choosing "same as this computer" restores it.
        self.assertEqual(i18n.setup(None), "de")
        self.assertEqual(os.environ["LANGUAGE"], "de:en")
        self.assertEqual(i18n._("Connect"), "Verbinden")

    def test_system_language_without_language_variable_is_restored_too(self):
        os.environ["LANG"] = "de_DE.UTF-8"
        i18n.setup("en")
        i18n.setup(None)
        self.assertNotIn("LANGUAGE", os.environ)


if __name__ == "__main__":
    unittest.main()
