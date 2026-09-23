"""Reviewed SHA-256 digests for the Windows inference files.

Each entry is tied to the matching immutable repository revision in engine.py.
Large-file digests come from the Hub's LFS metadata; Git-tracked files were
hashed from their pinned bytes and checked against the commit's Git blob ID.
Update this manifest together with the selected files and model revisions.
"""

MODEL_FILE_SHA256 = {
    "parakeet-tdt-0.6b-v3": {
        "config.json": "e747b85e1bdfd300c8b8ac63bac8dd5221f8fe9bc275b48d06c735fcd6971b6e",
        "model.safetensors": "3a2026366188c8c68598edbbff92f8d11590a08e0ae2e6775544e7b07d6a5e11",
        "tokenizer.json": "bd321b096832a3f270bd3b2a88823957920f1a5c5ada71114a26ea729d0cbe91",
        "generation_config.json": "b141de6ec6d7f982ece13f98f604e3fe1807ea9c0e839185d0ab7064604209d0",
        "tokenizer_config.json": "0b2fe0037599ee335f0b972fa682bf0ece74e4ccfec755cb7daa3405d3d3e874",
        "processor_config.json": "8346a93a3b987fa1dec57a78f045cd0817d21786589a5a096b41a57a446fd1d7",
    },
    "nemotron-speech-streaming-en-0.6b": {
        "config.json": "dffe850bc79ad2b0f8117804502b24d2c4a445aafbed4c1e40f8d78e0cb44065",
        "model.safetensors": "bddd8a7300826efd19cf7e01f1c7db8402bed6786fc4c7739632894f69c71473",
        "tokenizer.json": "60dc0361763fa3cd62df60f34fca3e61134676a939967853931db5f3869b2db2",
        "generation_config.json": "6ce531b39df8046cc8dbbe29dc64458b72972011d4717e160e6efb2c1af198d1",
        "tokenizer_config.json": "0665bee664daf39a155e5ee013bb1d885c58b4901994abc07b9a6b3904cce132",
        "processor_config.json": "cf35efc9abdd0963db7e96967f8a91d9c8243803b9f6bbd900aa936ee3ce42f3",
    },
    "moonshine-streaming-medium": {
        "config.json": "a7dced612603dc81ee2604242ccc69cb81131077b92a9a32eef8d6976b5d0efa",
        "model.safetensors": "1b431906c919f373c671ffe8516a0125a4e3ffca4a69255c7fd05782abf6a103",
        "tokenizer.json": "28bbc54a47dfa33673affa26010f23ac452685b18c76e81451d9b3ea25a54d54",
        "generation_config.json": "ff376268d9f8176da72ae0a3ca993778eb972a2f13a4ba0a914dbc3d6c832abe",
        "tokenizer_config.json": "93bb19c92de5c3d03e4a424ba033b2a81a32bdca6dc6f6dba176934dd8a9c6be",
        "processor_config.json": "a2325cec2c0f66ba9eadf11b0bc77930b25fa1af57e7c9e4fbbcf0cd3e620bf8",
        "preprocessor_config.json": "859e62520a4e44be5bf9559205898ec7adfd2952bdc4f4e62ea724ac6ba460eb",
        "special_tokens_map.json": "7755e8a96f77dea5458262fb120decdd6a654be977f3540bc781a0a3443f5974",
    },
    "base.en": {
        "config.json": "f3bc3821e9fc76a27bae538e11ae5b677dcdd352b4600429ce7951d398569aeb",
        "model.bin": "2a166925539a16005f14ff328359f9b9adb9dc4fb631bb3b227526862e93e2ef",
        "tokenizer.json": "929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df",
        "vocabulary.txt": "ff77588746d3a2595d32ab5b69ffd7b95ce2441ac57533cb66fc3eb575a115cf",
    },
    "small.en": {
        "config.json": "666a9605530ac1f61fa8177f3702b4dacec9966749e42610839fcc32661d5fae",
        "model.bin": "62b2a45b05ee59acb4a5341b33ee35e041395d378d418a18acfe4c9e768ee37a",
        "tokenizer.json": "929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df",
        "vocabulary.txt": "ff77588746d3a2595d32ab5b69ffd7b95ce2441ac57533cb66fc3eb575a115cf",
    },
    "medium.en": {
        "config.json": "4a1848ebabe7938d9797c15a2e8e4ce1d36e6fd4a43d096ae5955257c67c7962",
        "model.bin": "11b220779aea4c6f3ce9d2549c8a95ea869ed84066864b999531ef53e594fe5b",
        "tokenizer.json": "929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df",
        "vocabulary.txt": "ff77588746d3a2595d32ab5b69ffd7b95ce2441ac57533cb66fc3eb575a115cf",
    },
    "turbo": {
        "config.json": "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e",
        "model.bin": "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da",
        "tokenizer.json": "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd",
        "vocabulary.json": "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1",
        "preprocessor_config.json": "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711",
    },
}
