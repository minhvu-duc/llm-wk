import subprocess
from llmwiki.storage.content_store import ContentStore


def test_init_creates_git_repo_with_layout(tmp_path):
    cs = ContentStore(str(tmp_path), "kb")
    cs.init()
    root = tmp_path / "kb"
    assert (root / ".git").is_dir()
    assert (root / "sources").is_dir()
    assert (root / "wiki").is_dir()
    assert (root / "index.md").exists()
    assert (root / "log.md").exists()


def test_write_source_and_wiki_commits(tmp_path):
    cs = ContentStore(str(tmp_path), "kb"); cs.init()
    commit = cs.write_document(doc_id="d1", source_text="raw body",
                               wiki_text="# Title\nsummary", log_line="NEW d1")
    assert commit and len(commit) >= 7
    root = tmp_path / "kb"
    assert (root / "sources" / "d1.txt").read_text() == "raw body"
    assert (root / "wiki" / "d1.md").read_text() == "# Title\nsummary"
    assert "NEW d1" in (root / "log.md").read_text()
    out = subprocess.run(["git", "-C", str(root), "log", "--oneline"],
                         capture_output=True, text=True).stdout
    assert "d1" in out


def test_second_write_updates_same_files(tmp_path):
    cs = ContentStore(str(tmp_path), "kb"); cs.init()
    cs.write_document("d1", "v1 body", "# v1", "NEW d1")
    cs.write_document("d1", "v2 body", "# v2", "UPDATE d1")
    root = tmp_path / "kb"
    assert (root / "sources" / "d1.txt").read_text() == "v2 body"
    log = (root / "log.md").read_text()
    assert "NEW d1" in log and "UPDATE d1" in log
