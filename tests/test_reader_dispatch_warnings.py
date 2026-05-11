from src.core.reader import collect_input_bundle


def test_collect_input_bundle_emits_warning_for_unhandled_binary(tmp_path):
    bad = tmp_path / "blob.bin"
    bad.write_bytes(b"\x00\x01\x02\x03\x04\x05")

    bundle = collect_input_bundle(str(tmp_path))

    assert bundle["file_count"] == 0
    assert isinstance(bundle.get("warnings"), list)
    assert bundle["warnings"]
    assert "skip (" in bundle["warnings"][0]
