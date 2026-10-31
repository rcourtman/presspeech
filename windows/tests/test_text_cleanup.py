import unittest

import config
import text_cleanup
from text_cleanup import (
    ENGLISH_CLEANUP_ALL,
    ENGLISH_CLEANUP_DECLARED,
    ENGLISH_CLEANUP_NONE,
    LANGUAGE_ENGLISH_DECLARED,
    LANGUAGE_NOT_DECLARED,
    LANGUAGE_OTHER,
    apply_text_settings,
    dictation_language,
    english_cleanup_scope,
)

# Short and code-switched dictation in other languages with a real "um"/"er",
# English-looking words, shared words, or US spellings that are words in
# another language. Mirrored in the macOS self-tests and compared by
# scripts/check-text-cleanup-parity.py.
# text-cleanup-parity: begin non-english-dictation
NON_ENGLISH_DICTATION = (
    # German
    "Er sagt thanks.", "Er sagt thank you.", "Er meinte: I don't know.",
    "Er findet das nice.", "Er war right.", "Er hat ein Meeting today.",
    "Um sieben habe ich einen Call.", "Um ehrlich zu sein, it's fine.",
    "Er, the boss, kommt sp\u00e4ter.", "Er ist heute nicht da.",
    "Ich komme um acht Uhr, um dich abzuholen.", "Um acht.",
    "Kommt er? I don't know, maybe.", "Hat er, you know, the files?",
    "Morgen, sagt er, that's the deadline.", "Steig um, the train is right there.",
    "You know what? Er zieht um.", "Wer, er? Yes, him, the new guy.",
    "Wer war das? Er, I think.", "Lacht er I think.", "Er lacht laut.",
    "We have to leave now, um rechtzeitig anzukommen.",
    # Portuguese
    "Dei um like.", "Mandei um email today.", "Tenho um call.",
    "Vi um show really good.", "Um, dois, tr\u00eas.", "Eu tenho um carro novo.",
    "Me passa um, please, thanks.", "Comprei um, like, a new one, you know.",
    "Quantos? Um, I think.",
    # Danish, Dutch and Swedish
    "Han er the best.", "Han er, you know, the best.", "Er du ready?",
    "Det er nice.", "Det er godt.", "Hvem er? You know, the new guy.",
    "Er is.", "Er is een probleem met de printer.", "Is er, you know, a problem?",
    "Komt er? I don't think so.", "Ni har er bok.", "Tack till er, you know, really.",
    # Spanish and other US-spelling collisions
    "Me gusta the color.", "Por favor, cambia el color.", "Hazme ese favor, you know.",
    "Qual o valor? I don't know, maybe ten.", "Ins Labor, the results are there, I think.",
    "Fem kilometer, you know, that's far.", "Dialog, you know, is the key here.",
    "Tre\u0302s, I think you know the color.",
)
# text-cleanup-parity: end non-english-dictation

# Real words that must survive even with English declared: names, decomposed
# accents, set-apart words with no English beside them, shared marker words,
# and a quote next to "um".
# text-cleanup-parity: begin preserved-with-english-declared
PRESERVED_WITH_ENGLISH_DECLARED = (
    "We flew to Umm Qasr.", "Umm Kulthum sang.", "Ah Q is a character.",
    "Vzt\u030cah je dobry\u0301.", "Er, Peter.", "Um, Lisboa.", "Quantos? Um.",
    "Um, okay.", "Um.", "Had er been trouble?", "I said 'thanks' um and left.",
    "We need to um schedule it.", "Lacht er I think.", "Er lacht laut.",
    "Dei um like.", "I love the colores here.", "Offense.",
)
# text-cleanup-parity: end preserved-with-english-declared

# text-cleanup-parity: begin english-dictation
ENGLISH_DICTATION = {
    "Um, I think we should go.": "I think we should go.",
    "So, um, I was going to the store.": "So, I was going to the store.",
    "Er, can you send it tomorrow?": "Can you send it tomorrow?",
    "I was, um, thinking about it.": "I was, thinking about it.",
    "It's, er, not ready yet.": "It's, not ready yet.",
    "Um, yes.": "Yes.",
    "That's all, um.": "That's all.",
    "We need, um, more time.": "We need, more time.",
    "Um, the meeting is at three.": "The meeting is at three.",
    "Is that right, um?": "Is that right?",
    "This is it. Um, what do you think?": "This is it. What do you think?",
    "I'm um not sure about that.": "I'm not sure about that.",
    # Neither neighbour vouches for "um": kept even with English declared.
    "So um I think we should go.": "So um I think we should go.",
}
# text-cleanup-parity: end english-dictation


def settings(**overrides):
    values = {
        "dictionary": [],
        "remove_fillers": config.DEFAULTS["remove_fillers"],
        "british": config.DEFAULTS["british"],
        "dictation_language": config.DEFAULTS["dictation_language"],
        "suffix": "none",
    }
    values.update(overrides)
    return values


def english(text, **overrides):
    return apply_text_settings(text, settings(**overrides), LANGUAGE_ENGLISH_DECLARED)


class DefaultBehaviourTests(unittest.TestCase):
    """Without declared English, um, er and spelling are never changed."""

    def test_non_english_dictation_keeps_every_word(self):
        for text in NON_ENGLISH_DICTATION:
            with self.subTest(text=text):
                self.assertEqual(apply_text_settings(text, settings()), text)

    def test_english_dictation_keeps_um_and_er_without_declared_english(self):
        # The user-visible tradeoff of the safe default.
        for text in ENGLISH_DICTATION:
            with self.subTest(text=text):
                self.assertEqual(apply_text_settings(text, settings()), text)

    def test_language_neutral_fillers_are_still_removed(self):
        cases = {
            "Uh, I think so.": "I think so.",
            "Uh, er ist heute nicht da.": "Er ist heute nicht da.",
            "Det er, hmm, godt.": "Det er, godt.",
            "Eu tenho, ahh, um carro.": "Eu tenho, um carro.",
            "Ummm, erm, the file.": "The file.",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(apply_text_settings(text, settings()), expected)

    def test_spelling_is_unchanged_without_declared_english(self):
        for text in ("The color of the neighbor's car is gray.", "I realize."):
            with self.subTest(text=text):
                self.assertEqual(apply_text_settings(text, settings()), text)

    def test_scope_is_none_unless_english_is_declared(self):
        for language in (LANGUAGE_NOT_DECLARED, LANGUAGE_OTHER, "en", None):
            with self.subTest(language=language):
                self.assertEqual(
                    english_cleanup_scope("Um, I think we should go.", language),
                    ENGLISH_CLEANUP_NONE)


class DeclaredEnglishTests(unittest.TestCase):
    def test_english_fillers_are_removed(self):
        for text, expected in ENGLISH_DICTATION.items():
            with self.subTest(text=text):
                self.assertEqual(english(text), expected)

    def test_any_non_english_word_keeps_the_whole_dictation(self):
        for text in ("Um, das Meeting ist um neun, okay?",
                     "Okay, I'll call you later. Er kommt morgen.",
                     "Um, I don't think the Bahnhof und so is open.",
                     "Han er, you know, the best.",
                     "Wer war das? Er, I think."):
            with self.subTest(text=text):
                self.assertEqual(english_cleanup_scope(text, LANGUAGE_ENGLISH_DECLARED),
                                 ENGLISH_CLEANUP_NONE)
                self.assertEqual(english(text), text)

    def test_real_words_survive_declared_english(self):
        for text in PRESERVED_WITH_ENGLISH_DECLARED + ("Um acht.", "Vi um show really good."):
            with self.subTest(text=text):
                self.assertEqual(english(text), text)
                self.assertEqual(apply_text_settings(text, settings()), text)

    def test_set_apart_um_needs_an_english_word_in_its_sentence(self):
        self.assertEqual(english("Um, yes."), "Yes.")
        self.assertEqual(english("Er, Peter."), "Er, Peter.")
        self.assertEqual(english("Quantos? Um."), "Quantos? Um.")

    def test_abbreviations_are_never_fillers(self):
        for text in ("We took him to the ER.", "I studied at UM, which is in Michigan."):
            with self.subTest(text=text):
                self.assertEqual(english(text), text)
        self.assertEqual(english("Rush to the ER, um, now."), "Rush to the ER, now.")

    def test_identical_text_cannot_be_told_apart(self):
        # German "Er, I think." (he, I think) is the same characters as an
        # English filler. Declaring English accepts the English reading.
        self.assertEqual(english("Er, I think."), "I think.")
        self.assertEqual(apply_text_settings("Er, I think.", settings()), "Er, I think.")

    def test_british_spelling_is_judged_per_sentence(self):
        self.assertEqual(english("The color of the neighbor's car is gray."),
                         "The colour of the neighbour's car is grey.")
        # "color" is also Spanish; without an English word it keeps.
        self.assertEqual(english("Color, gray."), "Color, grey.")
        # English endings of a shared spelling change; Spanish plurals do not.
        self.assertEqual(english("The colors here."), "The colours here.")
        self.assertEqual(english("I love the colores here."), "I love the colores here.")
        self.assertEqual(
            english("Qual o valor? I don't know what the neighbor said."),
            "Qual o valor? I don't know what the neighbour said.")

    def test_settings_off_still_disable_each_step(self):
        text = "Um, the neighbor of the car."
        self.assertEqual(english(text, remove_fillers=False, british=False), text)
        self.assertEqual(english(text, british=False), "The neighbor of the car.")
        self.assertEqual(english(text, remove_fillers=False),
                         "Um, the neighbour of the car.")

    def test_rules_run_first_and_evidence_must_hold_after_them(self):
        self.assertEqual(
            english("Um, parake is the model I like for color.",
                    dictionary=[["parake", "Parakeet"]]),
            "Parakeet is the model I like for colour.")
        # The dictation-wide check reads what was spoken; the per-sentence
        # check reads what will be delivered.
        self.assertEqual(english("Um, I told the boss.",
                                 dictionary=[["the boss", "der Chef"]]),
                         "Um, I told der Chef.")
        self.assertEqual(english("Um, quero falar.",
                                 dictionary=[["quero falar", "I want to speak"]]),
                         "Um, I want to speak.")

    def test_filler_only_dictation(self):
        self.assertEqual(apply_text_settings("Uh.", settings(suffix="space")), "")
        # "Um" alone has no English beside it, so it stays even when declared.
        self.assertEqual(english("Um.", suffix="space"), "Um. ")
        self.assertEqual(apply_text_settings("Um.", settings(suffix="space")), "Um. ")


class DictationLanguageTests(unittest.TestCase):
    def test_setting_declares_english(self):
        self.assertEqual(dictation_language({"dictation_language": "en"}, {}),
                         LANGUAGE_ENGLISH_DECLARED)
        self.assertEqual(dictation_language({"dictation_language": "auto"}, {}),
                         LANGUAGE_NOT_DECLARED)
        self.assertEqual(dictation_language(None, None), LANGUAGE_NOT_DECLARED)

    def test_english_only_models_declare_english(self):
        for model in sorted(config.ENGLISH_ONLY_MODELS):
            with self.subTest(model=model):
                self.assertEqual(
                    dictation_language({}, {"backend": "whisper", "model_name": model}),
                    LANGUAGE_ENGLISH_DECLARED)

    def test_the_english_only_fallback_declares_nothing(self):
        timing = {"backend": "whisper", "model_name": "base.en"}
        self.assertEqual(dictation_language({}, timing, english_only_fallback=True),
                         LANGUAGE_NOT_DECLARED)
        self.assertEqual(
            dictation_language({"dictation_language": "en"}, timing,
                               english_only_fallback=True),
            LANGUAGE_ENGLISH_DECLARED)

    def test_parakeet_and_whisper_detection_never_declare_english(self):
        self.assertEqual(dictation_language(
            {}, {"backend": "parakeet", "model_name": "parakeet-tdt-0.6b-v3"}),
            LANGUAGE_NOT_DECLARED)
        self.assertEqual(dictation_language({}, {
            "backend": "whisper", "model_name": "turbo",
            "detected_language": "en", "detected_language_probability": 0.99}),
            LANGUAGE_NOT_DECLARED)

    def test_confident_other_language_vetoes_a_declaration(self):
        def language(code, probability):
            return dictation_language({"dictation_language": "en"}, {
                "backend": "whisper", "model_name": "turbo",
                "detected_language": code,
                "detected_language_probability": probability})

        self.assertEqual(language("de", 0.9), LANGUAGE_OTHER)
        self.assertEqual(language("de", 0.8), LANGUAGE_OTHER)
        self.assertEqual(language("de", 0.79), LANGUAGE_ENGLISH_DECLARED)
        self.assertEqual(language("de", True), LANGUAGE_ENGLISH_DECLARED)
        self.assertEqual(language("en", 0.99), LANGUAGE_ENGLISH_DECLARED)


class EvidenceTests(unittest.TestCase):
    def test_evidence_ignores_fillers_abbreviations_and_digits(self):
        evidence = text_cleanup.english_evidence
        self.assertEqual(evidence("EU OS um er uh"), (0, 0))
        # Sentence-initial "I" is the Slavic conjunction as often as English.
        self.assertEqual(evidence("I tak dalej."), (0, 1))
        self.assertEqual(evidence("Um, I see."), (1, 0))
        self.assertEqual(evidence("A 50 m\u00b2 flat, c\u2019est la vie, I think."), (2, 2))
        # A decomposed accent stays in its word, like a precomposed one.
        self.assertEqual(evidence("Tre\u0302s, I think."), (2, 1))
        self.assertEqual(evidence("Tr\u00eas, I think."), (2, 1))

    def test_neutral_fillers_keep_names_and_decomposed_accents(self):
        remove = text_cleanup._remove_fillers
        for text in ("We flew to Umm Qasr.", "Umm Kulthum sang.", "Ah Q is a character.",
                     "Vzt\u030cah je dobry\u0301."):
            with self.subTest(text=text):
                self.assertEqual(remove(text), text)
        self.assertEqual(remove("Uhh I think so."), "I think so.")
        self.assertEqual(remove("Umm, Kulthum sang."), "Kulthum sang.")


class FillerRemovalTests(unittest.TestCase):
    """The English filler behaviour moved here from app.py unchanged."""

    def test_default_removes_no_um_or_er(self):
        self.assertEqual(text_cleanup._remove_fillers("Um, uh, er, I was going."),
                         "Um, er, I was going.")

    def test_capitalization_and_punctuation_repairs(self):
        def remove(text):
            return text_cleanup._remove_fillers(text, english_fillers=ENGLISH_CLEANUP_ALL)
        self.assertEqual(remove("Um, it works."), "It works.")
        self.assertEqual(
            remove("This is the first sentence. Um this is the second sentence."),
            "This is the first sentence. This is the second sentence.")
        self.assertEqual(
            remove("This is not a boundary Um this stays lowercase."),
            "This is not a boundary this stays lowercase.")
        self.assertEqual(remove("So, um, uh, I was going."), "So, I was going.")
        self.assertEqual(remove("That's all, um."), "That's all.")
        self.assertEqual(remove("Um? What?"), "What?")

    def test_real_english_words_and_compounds_are_kept(self):
        for text in ("It works well.", "I might err.", "Yeah, uh-huh.",
                     "An ohm is a unit."):
            with self.subTest(text=text):
                self.assertEqual(text_cleanup._remove_fillers(
                    text, english_fillers=ENGLISH_CLEANUP_ALL), text)

    def test_scope_limits_which_um_and_er_go(self):
        remove = text_cleanup._remove_fillers
        text = "Um, uh, erm, ummm, hmm, er, ja."
        self.assertEqual(remove(text, english_fillers=ENGLISH_CLEANUP_NONE), "Um, er, ja.")
        self.assertEqual(remove(text, english_fillers="unexpected"), "Um, er, ja.")
        self.assertEqual(
            remove("Um, I think um we should, er, go.",
                   english_fillers=ENGLISH_CLEANUP_DECLARED),
            "I think um we should, go.")
        self.assertEqual(
            remove("Um, I think um we should, er, go.",
                   english_fillers=ENGLISH_CLEANUP_ALL),
            "I think we should, go.")


class SettingsCompatibilityTests(unittest.TestCase):
    def test_saved_text_settings_are_loaded_unchanged(self):
        for remove_fillers in (True, False):
            for british in (True, False):
                with self.subTest(remove_fillers=remove_fillers, british=british):
                    loaded = config._validated_settings({
                        "remove_fillers": remove_fillers, "british": british})
                    self.assertIs(loaded["remove_fillers"], remove_fillers)
                    self.assertIs(loaded["british"], british)

    def test_older_profiles_get_the_safe_language_default(self):
        loaded = config._validated_settings({"hotkey": "right alt",
                                             "remove_fillers": True, "british": True})
        self.assertEqual(loaded["dictation_language"], "auto")
        self.assertIs(loaded["remove_fillers"], True)
        self.assertIs(loaded["british"], True)

    def test_dictation_language_is_validated(self):
        self.assertEqual(config._validated_settings(
            {"dictation_language": "en"})["dictation_language"], "en")
        for value in ("de", "EN", "", 1, None, True):
            with self.subTest(value=value):
                self.assertEqual(config._validated_settings(
                    {"dictation_language": value})["dictation_language"], "auto")

    def test_english_only_model_list_matches_the_model_labels(self):
        labelled = {model for model, label in config.MODEL_LABELS.items()
                    if "English-only" in label}
        self.assertEqual(config.ENGLISH_ONLY_MODELS, labelled)
        self.assertTrue(config.ENGLISH_ONLY_MODELS <= set(config.MODELS))


if __name__ == "__main__":
    unittest.main()
