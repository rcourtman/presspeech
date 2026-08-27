"""British English spelling conversion (US -> GB word list).

Applied after transcription when the 'british' setting is enabled.
Inflexions are handled: realizes -> realises, colored -> coloured,
neighbors -> neighbours, mom's -> mum's.
"""

import re

BRITISH = {
    # -ize -> -ise
    "analyze": "analyse", "apologize": "apologise", "authorize": "authorise",
    "categorize": "categorise", "characterize": "characterise",
    "colonize": "colonise", "criticize": "criticise", "customize": "customise",
    "dramatize": "dramatise", "economize": "economise",
    "emphasize": "emphasise", "fertilize": "fertilise", "formalize": "formalise",
    "generalize": "generalise", "hypnotize": "hypnotise",
    "hypothesize": "hypothesise", "idealize": "idealise",
    "immobilize": "immobilise", "industrialize": "industrialise",
    "itemize": "itemise", "legalize": "legalise", "localize": "localise",
    "maximize": "maximise", "mechanize": "mechanise", "memorize": "memorise",
    "minimize": "minimise", "mobilize": "mobilise", "modernize": "modernise",
    "monopolize": "monopolise", "moralize": "moralise",
    "nationalize": "nationalise", "naturalize": "naturalise",
    "neutralize": "neutralise", "normalize": "normalise",
    "optimize": "optimise", "organize": "organise",
    "personalize": "personalise", "polarize": "polarise",
    "prioritize": "prioritise", "privatize": "privatise",
    "publicize": "publicise", "rationalize": "rationalise",
    "realize": "realise", "recognize": "recognise",
    "reorganize": "reorganise", "revolutionize": "revolutionise",
    "romanticize": "romanticise", "sanitize": "sanitise",
    "sensitize": "sensitise", "socialize": "socialise",
    "specialize": "specialise", "stabilize": "stabilise",
    "standardize": "standardise", "sterilize": "sterilise",
    "subsidize": "subsidise", "summarize": "summarise",
    "sympathize": "sympathise", "symbolize": "symbolise",
    "synthesize": "synthesise", "systematize": "systematise",
    "tantalize": "tantalise", "terrorize": "terrorise",
    "tranquilize": "tranquillise", "trivialize": "trivialise",
    "urbanize": "urbanise", "utilize": "utilise", "vandalize": "vandalise",
    "verbalize": "verbalise", "visualize": "visualise",
    "vitalize": "vitalise", "scrutinize": "scrutinise",
    "satirize": "satirise", "monopolize": "monopolise",
    # -or -> -our
    "armor": "armour", "armory": "armoury", "ardor": "ardour",
    "behavior": "behaviour", "candor": "candour", "clangor": "clangour",
    "color": "colour", "colorful": "colourful",
    "demeanor": "demeanour", "endeavor": "endeavour", "favor": "favour",
    "favorite": "favourite", "fervor": "fervour", "flavor": "flavour",
    "glamor": "glamour", "harbor": "harbour", "honor": "honour",
    "honorable": "honourable", "labor": "labour", "neighbor": "neighbour",
    "neighborhood": "neighbourhood", "odor": "odour", "parlor": "parlour",
    "rancor": "rancour", "rumor": "rumour", "savior": "saviour",
    "savor": "savour", "splendor": "splendour", "succor": "succour",
    "valor": "valour", "vigor": "vigour",
    # -er -> -re
    "caliber": "calibre", "center": "centre", "fiber": "fibre",
    "liter": "litre", "luster": "lustre", "maneuver": "manoeuvre",
    "meager": "meagre", "saber": "sabre", "scepter": "sceptre",
    "sepulcher": "sepulchre", "somber": "sombre", "specter": "spectre",
    "centimeter": "centimetre", "millimeter": "millimetre",
    "kilometer": "kilometre", "milliliter": "millilitre",
    # -og -> -ogue
    "analog": "analogue", "catalog": "catalogue", "demagog": "demagogue",
    "dialog": "dialogue", "epilog": "epilogue", "monolog": "monologue",
    "pedagog": "pedagogue", "prolog": "prologue", "travelog": "travelogue",
    # -se -> -ce
    "defense": "defence", "offense": "offence", "pretense": "pretence",
    # -l- / -ll-
    "canceled": "cancelled", "canceling": "cancelling",
    "counselor": "counsellor", "counseling": "counselling",
    "dialed": "dialled", "dialing": "dialling",
    "enroll": "enrol", "enrollment": "enrolment",
    "fueled": "fuelled", "fueling": "fuelling",
    "fulfill": "fulfil", "fulfillment": "fulfilment",
    "installment": "instalment", "instill": "instil",
    "jewelery": "jewellery", "jewelry": "jewellery",
    "labeled": "labelled", "labeling": "labelling",
    "leveling": "levelling", "marveling": "marvelling",
    "marvelous": "marvellous", "modeled": "modelled", "modeling": "modelling",
    "quarreled": "quarrelled", "quarreling": "quarrelling",
    "signaled": "signalled", "signaling": "signalling",
    "skillful": "skilful", "skilful": "skilful",
    "shoveling": "shovelling", "totaled": "totalled", "totaling": "totalling",
    "traveled": "travelled", "traveler": "traveller", "traveling": "travelling",
    "willful": "wilful", "woolen": "woollen", "worshiped": "worshipped",
    "worshiping": "worshipping", "worshipper": "worshipper",
    # medical / academic ae- oe-
    "anemia": "anaemia", "anesthesia": "anaesthesia", "anesthetic": "anaesthetic",
    "ameba": "amoeba", "archeology": "archaeology",
    "diarrhea": "diarrhoea", "edema": "oedema",
    "encyclopedia": "encyclopaedia", "esophagus": "oesophagus",
    "estrogen": "oestrogen", "etiology": "aetiology", "fetus": "foetus",
    "gynecology": "gynaecology", "gynecologist": "gynaecologist",
    "hemorrhage": "haemorrhage", "leukemia": "leukaemia",
    "orthopedic": "orthopaedic", "pediatric": "paediatric",
    "pediatrician": "paediatrician", "paleontology": "palaeontology",
    # other common pairs
    "airplane": "aeroplane", "aluminum": "aluminium", "artifact": "artefact",
    "cesium": "caesium", "cozy": "cosy", "curb": "kerb", "disk": "disc",
    "donut": "doughnut", "gray": "grey", "judgment": "judgement",
    "acknowledgment": "acknowledgement", "abridgment": "abridgement",
    "math": "maths", "mold": "mould", "molt": "moult", "mom": "mum",
    "mommy": "mummy", "moustache": "moustache", "mustache": "moustache",
    "pajamas": "pyjamas", "plow": "plough", "skeptic": "sceptic",
    "skeptical": "sceptical", "skepticism": "scepticism",
    "smolder": "smoulder", "sulfur": "sulphur", "tire": "tyre",
}

_TOKEN_RE = re.compile(r"[A-Za-z']+")


def to_british(text):
    def convert(match):
        token = match.group(0)
        lower = token.lower()
        repl = BRITISH.get(lower)
        if repl is None and lower.endswith("'s"):
            repl = BRITISH.get(lower[:-2])
            if repl is not None:
                repl += "'s"
        if repl is None:
            repl = _inflect(lower)
        if repl is None:
            return token
        if token.isupper():
            return repl.upper()
        if token[:1].isupper():
            return repl[:1].upper() + repl[1:]
        return repl

    return _TOKEN_RE.sub(convert, text)


def _inflect(lower):
    # ``analyze`` is the one listed -yze verb, so it does not enter the -ize
    # branches below even though its British inflections follow the same rule.
    if lower.endswith("yzing"):
        base = BRITISH.get(lower[:-3] + "e")
        if base:
            return base[:-1] + "ing" if base.endswith("e") else base + "ing"
    if lower.endswith("yzed"):
        base = BRITISH.get(lower[:-1])
        if base:
            return base + "d"
    if lower.endswith("izing"):
        base = BRITISH.get(lower[:-3] + "e")
        if base:
            return base[:-1] + "ing" if base.endswith("e") else base + "ing"
    if lower.endswith("ized"):
        base = BRITISH.get(lower[:-1])
        if base:
            return base + "d"
    if lower.endswith("izes"):
        base = BRITISH.get(lower[:-1])
        if base:
            return base + "s"
    for suffix in ("es", "s", "ed", "ing", "er", "est", "ly"):
        if lower.endswith(suffix):
            base = BRITISH.get(lower[:-len(suffix)])
            if base:
                # Some conversions add a silent e (center -> centre,
                # catalog -> catalogue). Apply English suffix rules to the
                # converted base instead of producing centreed/centreing.
                if suffix == "ed" and base.endswith("e"):
                    return base + "d"
                if suffix == "ing" and base.endswith("e"):
                    return base[:-1] + "ing"
                return base + suffix
    return None
