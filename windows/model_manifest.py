"""Pinned source and byte manifest for every Windows speech model.

The source revisions and inference-file contract are reviewed inputs. The
size/SHA-256 table is generated from those exact Hugging Face revisions by
``scripts/update-model-manifest.py --windows --write``. Keeping this in a
Python module makes the manifest part of the frozen application image rather
than a replaceable sidecar file. Full commit IDs also prevent mutable model
branches or faster-whisper's alias table from changing a fresh installation.
"""


MODEL_SOURCES = {
    "parakeet-tdt-0.6b-v3": (
        "nvidia/parakeet-tdt-0.6b-v3",
        "541d1f99c6b0c3cd0b11a95167540bb8edefd82b",
    ),
    "nemotron-speech-streaming-en-0.6b": (
        "nvidia/nemotron-speech-streaming-en-0.6b",
        "ebe59e5a817142986528bbbee5dba8db7b38ed50",
    ),
    "moonshine-streaming-medium": (
        "UsefulSensors/moonshine-streaming-medium",
        "57b843633a8c183cadf6699ffa761377a933a866",
    ),
    "base.en": (
        "Systran/faster-whisper-base.en",
        "3d3d5dee26484f91867d81cb899cfcf72b96be6c",
    ),
    "small.en": (
        "Systran/faster-whisper-small.en",
        "d1d751a5f8271d482d14ca55d9e2deeebbae577f",
    ),
    "medium.en": (
        "Systran/faster-whisper-medium.en",
        "a29b04bd15381511a9af671baec01072039215e3",
    ),
    "turbo": (
        "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
    ),
}

_TRANSFORMERS_REQUIRED = ("config.json", "model.safetensors", "tokenizer.json")
_TRANSFORMERS_OPTIONAL = (
    "generation_config.json",
    "tokenizer_config.json",
    "processor_config.json",
)

MODEL_REQUIRED_FILES = {
    "parakeet-tdt-0.6b-v3": _TRANSFORMERS_REQUIRED,
    "nemotron-speech-streaming-en-0.6b": _TRANSFORMERS_REQUIRED,
    "moonshine-streaming-medium": _TRANSFORMERS_REQUIRED,
    "base.en": ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"),
    "small.en": ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"),
    "medium.en": ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"),
    "turbo": ("config.json", "model.bin", "tokenizer.json", "vocabulary.json"),
}

MODEL_OPTIONAL_FILES = {
    "parakeet-tdt-0.6b-v3": _TRANSFORMERS_OPTIONAL,
    "nemotron-speech-streaming-en-0.6b": _TRANSFORMERS_OPTIONAL,
    "moonshine-streaming-medium": _TRANSFORMERS_OPTIONAL + (
        "preprocessor_config.json",
        "special_tokens_map.json",
    ),
    "turbo": ("preprocessor_config.json",),
}

MODEL_ALTERNATIVE_FILES = {
    "parakeet-tdt-0.6b-v3": (("processor_config.json",),),
    "nemotron-speech-streaming-en-0.6b": (("processor_config.json",),),
    "moonshine-streaming-medium": (
        ("processor_config.json", "preprocessor_config.json"),
    ),
}

# BEGIN GENERATED WINDOWS_MODEL_MANIFEST
MODEL_FILE_MANIFESTS = {
    "base.en": {
        "config.json": (2227, "f3bc3821e9fc76a27bae538e11ae5b677dcdd352b4600429ce7951d398569aeb"),
        "model.bin": (145216508, "2a166925539a16005f14ff328359f9b9adb9dc4fb631bb3b227526862e93e2ef"),
        "tokenizer.json": (2128466, "929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df"),
        "vocabulary.txt": (422309, "ff77588746d3a2595d32ab5b69ffd7b95ce2441ac57533cb66fc3eb575a115cf"),
    },
    "medium.en": {
        "config.json": (2643, "4a1848ebabe7938d9797c15a2e8e4ce1d36e6fd4a43d096ae5955257c67c7962"),
        "model.bin": (1527904330, "11b220779aea4c6f3ce9d2549c8a95ea869ed84066864b999531ef53e594fe5b"),
        "tokenizer.json": (2128466, "929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df"),
        "vocabulary.txt": (422309, "ff77588746d3a2595d32ab5b69ffd7b95ce2441ac57533cb66fc3eb575a115cf"),
    },
    "moonshine-streaming-medium": {
        "config.json": (1739, "a7dced612603dc81ee2604242ccc69cb81131077b92a9a32eef8d6976b5d0efa"),
        "generation_config.json": (167, "ff376268d9f8176da72ae0a3ca993778eb972a2f13a4ba0a914dbc3d6c832abe"),
        "model.safetensors": (1063634564, "1b431906c919f373c671ffe8516a0125a4e3ffca4a69255c7fd05782abf6a103"),
        "preprocessor_config.json": (195, "859e62520a4e44be5bf9559205898ec7adfd2952bdc4f4e62ea724ac6ba460eb"),
        "processor_config.json": (159, "a2325cec2c0f66ba9eadf11b0bc77930b25fa1af57e7c9e4fbbcf0cd3e620bf8"),
        "special_tokens_map.json": (96, "7755e8a96f77dea5458262fb120decdd6a654be977f3540bc781a0a3443f5974"),
        "tokenizer.json": (1676069, "28bbc54a47dfa33673affa26010f23ac452685b18c76e81451d9b3ea25a54d54"),
        "tokenizer_config.json": (172, "93bb19c92de5c3d03e4a424ba033b2a81a32bdca6dc6f6dba176934dd8a9c6be"),
    },
    "nemotron-speech-streaming-en-0.6b": {
        "config.json": (1284, "dffe850bc79ad2b0f8117804502b24d2c4a445aafbed4c1e40f8d78e0cb44065"),
        "generation_config.json": (192, "6ce531b39df8046cc8dbbe29dc64458b72972011d4717e160e6efb2c1af198d1"),
        "model.safetensors": (2472413604, "bddd8a7300826efd19cf7e01f1c7db8402bed6786fc4c7739632894f69c71473"),
        "processor_config.json": (525, "cf35efc9abdd0963db7e96967f8a91d9c8243803b9f6bbd900aa936ee3ce42f3"),
        "tokenizer.json": (400216, "60dc0361763fa3cd62df60f34fca3e61134676a939967853931db5f3869b2db2"),
        "tokenizer_config.json": (270, "0665bee664daf39a155e5ee013bb1d885c58b4901994abc07b9a6b3904cce132"),
    },
    "parakeet-tdt-0.6b-v3": {
        "config.json": (1153, "e747b85e1bdfd300c8b8ac63bac8dd5221f8fe9bc275b48d06c735fcd6971b6e"),
        "generation_config.json": (289, "b141de6ec6d7f982ece13f98f604e3fe1807ea9c0e839185d0ab7064604209d0"),
        "model.safetensors": (2508311120, "3a2026366188c8c68598edbbff92f8d11590a08e0ae2e6775544e7b07d6a5e11"),
        "processor_config.json": (392, "8346a93a3b987fa1dec57a78f045cd0817d21786589a5a096b41a57a446fd1d7"),
        "tokenizer.json": (1159960, "bd321b096832a3f270bd3b2a88823957920f1a5c5ada71114a26ea729d0cbe91"),
        "tokenizer_config.json": (290, "0b2fe0037599ee335f0b972fa682bf0ece74e4ccfec755cb7daa3405d3d3e874"),
    },
    "small.en": {
        "config.json": (2657, "666a9605530ac1f61fa8177f3702b4dacec9966749e42610839fcc32661d5fae"),
        "model.bin": (483545366, "62b2a45b05ee59acb4a5341b33ee35e041395d378d418a18acfe4c9e768ee37a"),
        "tokenizer.json": (2128466, "929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df"),
        "vocabulary.txt": (422309, "ff77588746d3a2595d32ab5b69ffd7b95ce2441ac57533cb66fc3eb575a115cf"),
    },
    "turbo": {
        "config.json": (2263, "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e"),
        "model.bin": (1617884929, "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da"),
        "preprocessor_config.json": (340, "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711"),
        "tokenizer.json": (2710337, "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd"),
        "vocabulary.json": (1068114, "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1"),
    },
}
# END GENERATED WINDOWS_MODEL_MANIFEST
