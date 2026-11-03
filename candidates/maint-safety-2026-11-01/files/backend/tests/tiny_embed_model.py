"""Test helper: a TINY but genuinely loadable embedding-model directory, built offline (no download, no `onnx` package needed).

model.onnx is a hand-encoded ONNX ModelProto (opset 13): out = Unsqueeze(Cast(input_ids, FLOAT), axes=[2]) -> (batch, seq, 1) float,
so onnxruntime really parses and runs it; tokenizer.json is written by the real `tokenizers` library. It stands in for the 470 MB
MiniLM only where a test needs "a valid cached model" without the network; the real file is exercised by the gated real-download test."""
from __future__ import annotations

import json
import os


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _f(num: int, wire: int, payload: bytes) -> bytes:
    return _varint((num << 3) | wire) + payload


def _ld(num: int, data: bytes) -> bytes:           # length-delimited field
    return _f(num, 2, _varint(len(data)) + data)


def _vi(num: int, val: int) -> bytes:              # varint field
    return _f(num, 0, _varint(val))


def _value_info(name: str, elem_type: int, dims) -> bytes:
    shape = b"".join(_ld(1, _ld(2, d.encode()) if isinstance(d, str) else _vi(1, d)) for d in dims)
    tensor = _vi(1, elem_type) + _ld(2, shape)
    return _ld(1, name.encode()) + _ld(2, _ld(1, tensor))


def tiny_onnx_bytes() -> bytes:
    cast = _ld(1, b"input_ids") + _ld(2, b"as_float") + _ld(3, b"cast") + _ld(4, b"Cast") + _ld(5, _ld(1, b"to") + _vi(3, 1) + _vi(20, 2))
    unsq = _ld(1, b"as_float") + _ld(1, b"axes") + _ld(2, b"last_hidden_state") + _ld(3, b"unsq") + _ld(4, b"Unsqueeze")
    axes = _vi(1, 1) + _vi(2, 7) + _ld(8, b"axes") + _ld(9, (2).to_bytes(8, "little"))      # int64 [1] = [2]
    graph = (_ld(1, cast) + _ld(1, unsq) + _ld(2, b"tiny") + _ld(5, axes)
             + _ld(11, _value_info("input_ids", 7, ["batch", "seq"])) + _ld(11, _value_info("attention_mask", 7, ["batch", "seq"]))
             + _ld(12, _value_info("last_hidden_state", 1, ["batch", "seq", 1])))
    return _vi(1, 8) + _ld(7, graph) + _ld(8, _vi(2, 13))


def write_tiny_model(dest: str) -> None:
    from tokenizers import Tokenizer, models, pre_tokenizers
    os.makedirs(os.path.join(dest, "onnx"), exist_ok=True)
    os.makedirs(os.path.join(dest, "1_Pooling"), exist_ok=True)
    with open(os.path.join(dest, "onnx", "model.onnx"), "wb") as f:
        f.write(tiny_onnx_bytes())
    tok = Tokenizer(models.WordLevel({"[UNK]": 0, "brake": 1, "pad": 2}, unk_token="[UNK]"))
    tok.pre_tokenizer = pre_tokenizers.Whitespace()
    tok.save(os.path.join(dest, "tokenizer.json"))
    for rel in ("tokenizer_config.json", "special_tokens_map.json", "config.json", "sentence_bert_config.json", "1_Pooling/config.json"):
        with open(os.path.join(dest, rel), "w") as f:
            json.dump({"fixture": True}, f)
