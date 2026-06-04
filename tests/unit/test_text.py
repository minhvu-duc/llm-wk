from llmwiki.text import normalize, content_hash, shingles, jaccard


def test_normalize_collapses_whitespace_and_nfc():
    assert normalize("  Hello\t\nworld  ") == "Hello world"


def test_content_hash_stable_and_normalization_insensitive():
    assert content_hash("Hello   world") == content_hash(" Hello world ")
    assert content_hash("a") != content_hash("b")
    assert len(content_hash("x")) == 64  # sha256 hex


def test_jaccard_identical_is_one_and_disjoint_is_zero():
    a = shingles("the quick brown fox", n=2)
    assert jaccard(a, a) == 1.0
    assert jaccard(shingles("alpha beta", n=2), shingles("gamma delta", n=2)) == 0.0
