import unittest

from british import BRITISH, to_british


class BritishInflectionTests(unittest.TestCase):
    def test_every_ize_entry_converts_common_inflections(self):
        entries = [
            (american, british)
            for american, british in BRITISH.items()
            if american.endswith("ize")
        ]
        self.assertTrue(entries)
        for american, british in entries:
            with self.subTest(word=american):
                self.assertEqual(to_british(american + "d"), british + "d")
                self.assertEqual(to_british(american + "s"), british + "s")
                self.assertEqual(
                    to_british(american[:-1] + "ing"),
                    british[:-1] + "ing",
                )

    def test_ize_inflections_preserve_capitalization(self):
        self.assertEqual(to_british("REALIZED"), "REALISED")
        self.assertEqual(to_british("Organizing"), "Organising")

    def test_unlisted_ize_like_words_are_unchanged(self):
        self.assertEqual(
            to_british("She prized the capsized model."),
            "She prized the capsized model.",
        )

    def test_existing_regular_and_possessive_inflections_still_convert(self):
        self.assertEqual(
            to_british("The colored neighbors liked Mom's favorite."),
            "The coloured neighbours liked Mum's favourite.",
        )


if __name__ == "__main__":
    unittest.main()
