"""Transcript text cleanup: dictionary rules, filler removal and spelling.

Dependency-free so the text path can be tested on any platform. The macOS
app implements the same rule in ``swift/Sources/Presspeech/main.swift``;
``scripts/check-text-cleanup-parity.py`` keeps the shared lists identical.

Some cleanup is only correct for English text. "um" is a Portuguese article
and numeral and a German preposition, "er" is a German, Dutch, Danish and
Swedish word, and US-to-British spelling rewrites Spanish "color" and
"favor". Text alone cannot tell them apart: German "Wer war das? Er, I
think." and an English "Er, I think." are the same characters. So these
steps run only when the user has declared English dictation: the
"My dictation is in English" setting, or an English-only model. Without
that, "um" and "er" are kept and spelling is not changed.

Within declared English, word lists can only keep words, never justify
removing one:

- any non-English marker in the dictation, or a confident Whisper detection
  of another language, disables English-only cleanup for that dictation;
- an "um"/"er" is kept when its sentence has a non-English marker, when it is
  an all-capital abbreviation ("ER", "UM"), and when it sits inside a phrase
  whose neighbouring words are not both English ("lacht er I think");
- an "um"/"er" goes when its sentence has a common English word and
  punctuation sets it apart ("Um, I think", "I was, um, going"), or when the
  words on both sides are common English words ("I'm um not");
- British spelling changes only sentences without non-English markers, and
  leaves US spellings that are also words in another language ("color",
  "liter", "dialog") unless the sentence has an English word.
"""

import re
import unicodedata

import config as cfg
from british import to_british

FILLER_BOUNDARY_WRAPPERS = "\"'\u201c\u201d\u2018\u2019([{"
FILLER_SENTENCE_TERMINATORS = ".!?"
FILLER_ORPHAN_SEPARATORS = ",.;:!?"

# Fillers that are not common words in the languages Presspeech's models
# transcribe. A capitalised one that reads as part of a name ("Umm Qasr",
# "Ah Q") is kept; see _neutral_filler_removable.
# text-cleanup-parity: begin neutral-fillers
NEUTRAL_FILLER_PATTERNS = ("umm+", "uh+", "ah+", "erm", "hm+")
# text-cleanup-parity: end neutral-fillers
# English fillers that are also common words in other supported languages.
# text-cleanup-parity: begin english-only-fillers
ENGLISH_ONLY_FILLER_PATTERNS = ("um", "er")
# text-cleanup-parity: end english-only-fillers


# Word characters around a filler, including combining accent marks, so a
# decomposed "vzt\u030cah" is one word rather than "vzt" plus "ah".
_FILLER_WORD_CHARACTER = r"[\w\u0300-\u036f\u1ab0-\u1aff\u1dc0-\u1dff\u20d0-\u20ff\ufe20-\ufe2f'-]"


def _filler_regex(patterns):
    return re.compile(
        r"(?<!%s)(?:%s)(?!%s)" % (
            _FILLER_WORD_CHARACTER, "|".join(patterns), _FILLER_WORD_CHARACTER),
        re.IGNORECASE)


FILLER_RE = _filler_regex(
    ENGLISH_ONLY_FILLER_PATTERNS + NEUTRAL_FILLER_PATTERNS)
NEUTRAL_FILLER_RE = _filler_regex(NEUTRAL_FILLER_PATTERNS)

# What is known about the dictation language before its words are read.
LANGUAGE_ENGLISH_DECLARED = "english-declared"  # setting or English-only model
LANGUAGE_NOT_DECLARED = "not-declared"
LANGUAGE_OTHER = "other-language"  # confidently detected non-English
# faster-whisper reports a probability for the language it detected. A
# confident non-English detection vetoes English-only cleanup; a detection of
# English never enables it.
MIN_DETECTED_LANGUAGE_PROBABILITY = 0.8

# How much English-only cleanup a dictation may receive.
ENGLISH_CLEANUP_ALL = "all"  # every "um"/"er"; only for known-English callers
ENGLISH_CLEANUP_DECLARED = "declared-english"
ENGLISH_CLEANUP_NONE = "none"
# text-cleanup-parity: begin punctuation
# After an "um"/"er", one of these sets it apart from the following words.
ENGLISH_FILLER_DELIMITERS = ",.?!;:\u2026"
# Sentence ends for per-sentence evidence and the sentence-initial "I" rule.
SENTENCE_BREAKS = ".!?\u2026\n\r"
# French elisions such as c'est, j'ai, l'eau and qu'il.
FRENCH_ELISION_PREFIXES = ("c'", "d'", "j'", "l'", "m'", "n'", "s'", "t'", "qu'")
# text-cleanup-parity: end punctuation

# Frequent English words that are not common words in another supported
# language. Shared words such as "is", "to", "in", "was" and "die" are
# deliberately absent: one of them in a short German or Dutch sentence must
# not make that sentence look English.
# text-cleanup-parity: begin english-markers
ENGLISH_MARKERS = frozenset((
    "the", "and", "you", "that", "this", "with", "not", "but",
    "they", "what", "your", "there", "their", "would", "could", "should",
    "which", "about", "our", "she", "him", "his", "were", "from",
    "because", "very", "really", "know", "think", "going", "yes", "please",
    "thank", "thanks", "it", "if", "when", "where", "why",
    "who", "how", "them", "these", "those", "does", "did", "got",
    "need", "like", "can", "some", "any", "other", "only",
    "here", "now", "well", "good", "right", "sure", "maybe", "something",
    "anything", "nothing", "everything", "someone", "people", "today",
    "tomorrow", "yesterday", "call", "us", "are", "being", "than", "then",
    "into", "after", "before", "again", "much", "many", "such", "own",
    "same", "each", "every", "both", "few", "while", "until", "since",
    "though", "although", "yet", "ever", "never", "always", "often",
    "already", "around", "through", "without", "between", "against",
    "during", "another", "else", "whether", "i'm", "i've", "i'll", "i'd",
    "it's", "that's", "there's", "what's", "let's", "don't", "doesn't",
    "didn't", "can't", "won't", "isn't", "aren't", "wasn't", "weren't",
    "you're", "you've", "you'll", "we're", "we've", "we'll", "they're",
    "they've", "couldn't", "wouldn't", "shouldn't", "haven't", "hasn't",
))
# text-cleanup-parity: end english-markers

# Frequent words of the other supported Latin-script languages that are not
# English words. Any token with a non-ASCII letter or mark also counts. A
# false hit here only keeps words.
# text-cleanup-parity: begin non-english-markers
NON_ENGLISH_MARKERS = frozenset((
    # German
    "der", "das", "und", "ist", "nicht", "ich", "sie", "ein", "eine",
    "einen", "einem", "einer", "zu", "mit", "auf", "sich", "des", "auch",
    "wir", "ihr", "wie", "aber", "oder", "wenn", "noch", "nach", "bei",
    "aus", "sind", "haben", "wird", "werden", "kann", "dass", "schon",
    "nur", "jetzt", "hier", "heute", "mir", "mich", "dich", "uns", "sehr",
    "nein", "kein", "keine", "diese", "dieser", "dieses", "vom", "zum",
    "zur", "im", "es", "ja", "doch", "mal", "gibt", "gut", "kommt", "geht",
    "macht", "sagt", "habe", "hatte", "gestern", "morgen", "immer",
    "wieder", "ins", "zieht", "wer",
    # Dutch
    "het", "een", "ik", "niet", "dat", "op", "zijn", "voor", "maar", "ook",
    "als", "bij", "nog", "naar", "wat", "deze", "dit", "hij", "zij", "wij",
    "jij", "heb", "hebben", "worden", "wordt", "geen", "niets", "niks",
    "wel", "mijn", "jouw", "uw", "ons", "onze", "waar", "hoe", "omdat",
    "alleen", "moet", "kunnen", "zou", "veel", "en", "de", "om", "komt",
    "gaat", "zegt", "heeft", "staat", "gisteren", "twee",
    # Danish and Swedish
    "og", "ikke", "det", "jeg", "som", "har", "af", "var", "du", "vi",
    "kan", "hvad", "hvor", "eller", "efter", "skal", "vil", "blev", "nu",
    "sig", "mig", "dig", "os", "hun", "han", "hende", "meget", "godt",
    "rigtig", "lige", "nej", "och", "att", "inte", "ett", "jag", "av",
    "hon", "vad", "mycket", "kommer", "sin", "sitt", "sina", "bara", "ska",
    "med", "siger", "laver", "fem", "hvem", "tack", "säger",
    # Portuguese
    "que", "uma", "da", "em", "para", "por", "se", "na", "mais", "mas",
    "eu", "ele", "ela", "isso", "isto", "este", "esta", "muito", "foi",
    "tem", "ter", "nos", "pelo", "pela", "ao", "dos", "seu", "sua", "meu",
    "minha", "quando", "onde", "porque", "aqui", "obrigado", "obrigada",
    "tenho", "temos", "vou", "vai", "fazer", "tudo", "nada", "sim", "bem",
    "quero", "passa", "pega", "tipo", "comprei", "mandei", "dois", "tres",
    # Spanish
    "el", "la", "del", "y", "un", "una", "con", "su", "lo", "le", "pero",
    "muy", "sobre", "donde", "quien", "cuando", "esto", "eso", "ya", "ella",
    "nosotros", "gracias", "hola", "entonces", "estoy", "fue", "tiene",
    "hace", "tengo", "voy", "todo", "bien", "mucho", "ese", "gusta",
    # French
    "les", "et", "est", "pas", "je", "tu", "il", "elle", "nous", "vous",
    "ils", "elles", "qui", "dans", "pour", "sur", "avec", "ce", "cette",
    "ou", "aussi", "avoir", "fait", "tout", "rien", "merci", "oui", "non",
    # Italian
    "gli", "di", "che", "sono", "della", "delle", "dei", "nel", "nella",
    "anche", "molto", "questo", "questa", "mi", "ti", "ci", "si", "grazie",
    "sei", "siamo", "essere", "fare", "tutto", "niente", "bene", "cosa",
    # Polish, Czech and Slovak
    "w", "z", "nie", "jest", "co", "jak", "ale", "tak", "czy", "mam",
    "jestem", "tylko", "bardzo", "dla", "od", "po", "przez", "przy", "oraz",
    "tego", "tym", "jsem", "jsou", "jen", "aby", "nebo", "sa",
    # Slovenian, Croatian, Bosnian and Serbian
    "smo", "ste", "nije", "kao", "ako", "ili", "za", "iz", "pa", "kad",
    "kada", "samo", "jako", "ima", "nema", "biti", "bila", "hvala",
    # Romanian
    "pe", "cu", "mai", "sunt", "sau", "foarte", "aici", "acum", "dar",
    "fost", "fi", "ea", "noi", "voi", "lor",
    # Hungarian
    "az", "hogy", "nem", "egy", "csak", "ez", "azt", "igen", "nagyon",
    "lesz", "vagy", "itt", "ott",
    # Finnish and Estonian
    "ei", "mutta", "kun", "niin", "oli", "myös", "kanssa", "joka", "sitten",
    "kiitos", "ta", "mis", "kui", "aga", "ning", "ka", "nii", "siis", "nad",
    "olen", "jah",
    # Latvian and Lithuanian
    "ir", "ar", "uz", "kas", "tas", "nav", "jau", "tai", "yra", "ne",
    "kaip", "jis", "ji", "mes", "labai", "taip",
    # Maltese
    "fil", "minn", "mhux", "huwa", "hija", "jien", "int", "grazzi", "iva",
))
# text-cleanup-parity: end non-english-markers

_APOSTROPHES = "'\u2019"


def _is_token_character(character):
    # Letters plus combining marks, so a decomposed accent stays in its word.
    return character.isalpha() or unicodedata.category(character).startswith("M")


def _letter_tokens(text):
    """Yield (start, token) for runs of letters with inner apostrophes.

    Letters and marks are the Unicode L and M categories, the same set as the
    macOS ``[\\p{L}\\p{M}]`` tokenizer, so both apps score the same words.
    """
    index, length = 0, len(text)
    while index < length:
        if not text[index].isalpha():
            index += 1
            continue
        start = index
        index += 1
        while index < length and (_is_token_character(text[index])
                                  or text[index] in _APOSTROPHES):
            index += 1
        yield start, text[start:index]


def _clause_start_before(text, start, lower_bound=0):
    """Whether only spaces or quotes separate ``start`` from a sentence start."""
    index = start
    while index > lower_bound:
        previous = text[index - 1]
        if previous in " \t" or previous in FILLER_BOUNDARY_WRAPPERS:
            index -= 1
            continue
        return previous in SENTENCE_BREAKS
    return True


def _is_abbreviation(token):
    return len(token) > 1 and token.isupper()


def _english_token(text, start, token):
    """Classify one token: "english", "other", or None for neither."""
    raw = token.replace("\u2019", "'").strip("'")
    if not raw or FILLER_RE.fullmatch(raw):
        return None
    if raw == "I":
        # Sentence-initial "I" is the Slavic conjunction as often as English.
        return None if _clause_start_before(text, start) else "english"
    if _is_abbreviation(raw):
        return None
    lowered = raw.lower()
    if lowered in ENGLISH_MARKERS:
        return "english"
    if (lowered in NON_ENGLISH_MARKERS or not lowered.isascii()
            or any(lowered.startswith(prefix) and len(lowered) > len(prefix)
                   for prefix in FRENCH_ELISION_PREFIXES)):
        return "other"
    return None


def english_evidence(text):
    """Count English and non-English marker tokens in a transcript.

    Returns ``(english, other)``. Filler candidates and all-capital
    abbreviations such as "EU" or "OS" are ignored.
    """
    english = other = 0
    for start, token in _letter_tokens(text):
        kind = _english_token(text, start, token)
        english += kind == "english"
        other += kind == "other"
    return english, other


def english_cleanup_scope(text, language=LANGUAGE_NOT_DECLARED):
    """Return how much English-only cleanup the recognizer text may receive.

    Only declared English enables it; the words can still veto it. Each
    "um"/"er" is then judged on its own (see ``_english_filler_removable``).
    """
    if language != LANGUAGE_ENGLISH_DECLARED:
        return ENGLISH_CLEANUP_NONE
    _english, other = english_evidence(text)
    return ENGLISH_CLEANUP_NONE if other else ENGLISH_CLEANUP_DECLARED


def _sentence_bounds(text, start, end):
    """Bounds of the sentence containing ``text[start:end]``."""
    sentence_start = start
    while sentence_start > 0 and text[sentence_start - 1] not in SENTENCE_BREAKS:
        sentence_start -= 1
    sentence_end = end
    while sentence_end < len(text):
        sentence_end += 1
        if text[sentence_end - 1] in SENTENCE_BREAKS:
            break
    return sentence_start, sentence_end


def _sentence_spans(text):
    """Consecutive spans covering ``text``, each ending after its break."""
    spans, start = [], 0
    for index, character in enumerate(text):
        if character in SENTENCE_BREAKS:
            spans.append((start, index + 1))
            start = index + 1
    if start < len(text):
        spans.append((start, len(text)))
    return spans


def _boundary_before(text, start, sentence_start):
    """A sentence start, or a comma, before the word (spaces aside)."""
    if _clause_start_before(text, start, sentence_start):
        return True
    index = start
    while index > sentence_start and text[index - 1] in " \t":
        index -= 1
    return index > sentence_start and text[index - 1] == ","


def _neighbour_is_english(text, start, end, step, sentence_start, sentence_end):
    """Whether the word right before (step -1) or after (+1) is English.

    Only spaces may separate them; a quote or apostrophe next to the space
    ("'thanks' um") is punctuation, not part of the word.
    """
    index = start - 1 if step < 0 else end
    while sentence_start <= index < sentence_end and text[index] in " \t":
        index += step
    if (not sentence_start <= index < sentence_end
            or not _is_token_character(text[index])):
        return False
    token_start = token_end = index
    while (token_start > sentence_start
           and (_is_token_character(text[token_start - 1])
                or text[token_start - 1] in _APOSTROPHES)):
        token_start -= 1
    while (token_end < sentence_end
           and (_is_token_character(text[token_end])
                or text[token_end] in _APOSTROPHES)):
        token_end += 1
    return _english_token(text, token_start, text[token_start:token_end]) == "english"


def _english_filler_removable(text, start, end, scope):
    """Whether this "um"/"er" may go under ``scope``."""
    if _is_abbreviation(text[start:end]):
        return False
    if scope == ENGLISH_CLEANUP_ALL:
        return True
    if scope != ENGLISH_CLEANUP_DECLARED:
        return False
    sentence_start, sentence_end = _sentence_bounds(text, start, end)
    english, other = english_evidence(text[sentence_start:sentence_end])
    if other:
        return False
    before = _boundary_before(text, start, sentence_start)
    if before and end < len(text) and text[end] in ENGLISH_FILLER_DELIMITERS:
        # Set apart ("Um, I think"), but German "Er, Peter." has no English.
        return english >= 1
    before = before or _neighbour_is_english(
        text, start, end, -1, sentence_start, sentence_end)
    return before and _neighbour_is_english(
        text, start, end, 1, sentence_start, sentence_end)


def _neutral_filler_removable(text, start, end):
    """Keep a capitalised "Umm"/"Ah" that reads as part of a name.

    Recognizers capitalise a filler only at a sentence start, so "Umm Qasr"
    mid-sentence is a name, and a capitalised word straight after a
    sentence-initial one ("Umm Kulthum", "Ah Q") may be too. "I" is not a name.
    """
    if not text[start:end][:1].isupper():
        return True
    if not _clause_start_before(text, start):
        return False
    index = end
    while index < len(text) and text[index] in " \t":
        index += 1
    if index == end or index >= len(text) or not text[index].isupper():
        return True
    word_end = index
    while word_end < len(text) and _is_token_character(text[word_end]):
        word_end += 1
    return text[index:word_end] == "I"


def dictation_language(settings, timing=None, *, english_only_fallback=False):
    """What the user and the recognizer say about this dictation's language.

    English is declared by the "dictation_language" setting, or by choosing
    an English-only model (``timing["model_name"]``, recorded with the text).
    The English-only fallback after a Parakeet failure declares nothing: the
    user chose a multilingual model. A confident Whisper detection of another
    language vetoes a declaration; a detection of English never makes one.
    """
    settings = settings if isinstance(settings, dict) else {}
    timing = timing if isinstance(timing, dict) else {}
    language = timing.get("detected_language")
    probability = timing.get("detected_language_probability")
    if (timing.get("backend") == "whisper"
            and isinstance(language, str) and language
            and language.lower() != "en"
            and isinstance(probability, (int, float))
            and not isinstance(probability, bool)
            and probability >= MIN_DETECTED_LANGUAGE_PROBABILITY):
        return LANGUAGE_OTHER
    if settings.get("dictation_language") == "en":
        return LANGUAGE_ENGLISH_DECLARED
    if (not english_only_fallback
            and timing.get("model_name") in cfg.ENGLISH_ONLY_MODELS):
        return LANGUAGE_ENGLISH_DECLARED
    return LANGUAGE_NOT_DECLARED


def _filler_capitalization_targets(text, matches):
    """Locate sentence starts whose capitalized filler carried the casing."""
    targets = set()
    for match in matches:
        filler = match.group(0)
        if not filler or not filler[0].isupper():
            continue
        index = match.start()
        while index > 0:
            previous = text[index - 1]
            if previous.isspace() or previous in FILLER_BOUNDARY_WRAPPERS:
                index -= 1
                continue
            if previous in FILLER_SENTENCE_TERMINATORS:
                targets.add(sum(
                    character in FILLER_SENTENCE_TERMINATORS
                    for character in text[:index]))
            break
        else:
            targets.add(0)
    return targets


def _restore_filler_capitalization(text, targets):
    if not text or not targets:
        return text
    result = []
    terminator_ordinal = 0
    should_capitalize = 0 in targets
    for character in text:
        if should_capitalize:
            if character.islower():
                result.append(character.upper())
                should_capitalize = False
                continue
            if character.isalpha() or character.isdigit():
                should_capitalize = False

        result.append(character)
        if character in FILLER_SENTENCE_TERMINATORS:
            terminator_ordinal += 1
            if terminator_ordinal in targets:
                should_capitalize = True
        elif (should_capitalize and not character.isspace()
              and character not in FILLER_BOUNDARY_WRAPPERS
              and character not in FILLER_ORPHAN_SEPARATORS):
            should_capitalize = False
    return "".join(result)


def _remove_fillers(text, *, english_fillers=ENGLISH_CLEANUP_NONE):
    """Remove conservative filler words and repair their punctuation/casing.

    ``english_fillers`` is an ``english_cleanup_scope(...)`` result deciding
    which "um" and "er" go; they are real words in other languages, so the
    default and any unknown value remove none. Language-neutral fillers go
    unless they read as part of a name.
    """
    matches = [
        match for match in FILLER_RE.finditer(text)
        if (_neutral_filler_removable(text, match.start(), match.end())
            if NEUTRAL_FILLER_RE.fullmatch(match.group(0))
            else _english_filler_removable(
                text, match.start(), match.end(), english_fillers))
    ]
    if not matches:
        return text
    capitalization_targets = _filler_capitalization_targets(text, matches)
    pieces, last = [], 0
    for match in matches:
        pieces.append(text[last:match.start()])
        last = match.end()
    pieces.append(text[last:])
    result = "".join(pieces)
    # Removing a filler must not leave comma runs, punctuation pairs, or a
    # lowercase sentence start when the filler carried the capital letter.
    result = re.sub(r"\s*,(?:\s*,)+", ",", result)
    result = re.sub(r"([.!?])\s+[,.;:!?]+\s*", r"\1 ", result)
    result = re.sub(r"\s+([.,!?;:])", r"\1", result)
    result = re.sub(r",+([.!?;:])", r"\1", result)
    result = re.sub(r"\s+", " ", result)
    result = re.sub(r"^[\s,.;:!?]+", "", result).strip()
    return _restore_filler_capitalization(result, capitalization_targets)


def _british_spelling(text, scope):
    """Respell sentences of declared-English dictation without other words."""
    if scope == ENGLISH_CLEANUP_ALL:
        return to_british(text)
    if scope != ENGLISH_CLEANUP_DECLARED:
        return text
    pieces = []
    for start, end in _sentence_spans(text):
        sentence = text[start:end]
        english, other = english_evidence(sentence)
        if other:
            pieces.append(sentence)
            continue
        # "color", "liter" and "dialog" are also Spanish, German or Danish
        # words; respell them only beside an English word.
        pieces.append(to_british(sentence, keep_shared_spellings=english == 0))
    return "".join(pieces)


def _apply_dictionary_rules(text, rules, *, return_effects=False):
    """Apply longest non-overlapping rules once against the original text."""
    active = [
        (index, spoken, replacement)
        for index, (spoken, replacement) in enumerate(
            cfg.validated_dictionary(rules) or [])
    ]
    # Prefer the most specific phrase regardless of the order rules were added.
    # The original order remains a deterministic tie-breaker for equal phrases.
    active.sort(key=lambda rule: (-len(rule[1]), rule[1].casefold(), rule[0]))
    matches = []
    for _index, spoken, replacement in active:
        pattern = r"(?<!\w)%s(?!\w)" % re.escape(spoken)
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            start, end = match.span()
            if any(start < other_end and other_start < end
                   for other_start, other_end, _replacement in matches):
                continue
            matches.append((start, end, replacement))

    # Every range belongs to the untouched transcript, so replacement text is
    # inserted literally and can never trigger a later dictionary rule.
    for start, end, replacement in sorted(matches, reverse=True):
        text = text[:start] + replacement + text[end:]
    if return_effects:
        return (text, any(not replacement for _, _, replacement in matches),
                any(bool(replacement) for _, _, replacement in matches))
    return text


def apply_text_settings(text, settings, language=LANGUAGE_NOT_DECLARED):
    """Apply dictionary rules, filler removal, spelling and the suffix.

    The order is unchanged: user dictionary rules first, then fillers, then
    British spelling. ``language`` comes from ``dictation_language``; the
    dictation-wide check reads the recognizer's text before any rule
    rewrites it.
    """
    scope = english_cleanup_scope(text, language)
    text, removed_by_rule, inserted_by_rule = _apply_dictionary_rules(
        text, settings["dictionary"], return_effects=True)
    if settings["remove_fillers"]:
        text = _remove_fillers(text, english_fillers=scope)
    if settings.get("british"):
        text = _british_spelling(text, scope)
    if (not text or
            (removed_by_rule and not inserted_by_rule and
             all(char.isspace() or unicodedata.category(char).startswith("P")
                 for char in text))):
        # An empty replacement can leave the recognizer's trailing period
        # behind. A punctuation-only remnant is not worth replacing the
        # user's selected text with; an explicit punctuation replacement
        # remains deliverable because inserted_by_rule is true.
        return ""
    text += cfg.SUFFIXES.get(settings["suffix"], " ")
    return text
