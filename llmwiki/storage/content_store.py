from __future__ import annotations
import os
import subprocess


class ContentStore:
    """Git-backed per-collection content store. Layout:
       <root>/<collection>/{sources/, wiki/, index.md, log.md}"""

    def __init__(self, root: str, collection: str):
        self.repo = os.path.join(root, collection)

    def _git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", self.repo, *args],
                              capture_output=True, text=True, check=True).stdout.strip()

    def init(self) -> None:
        os.makedirs(os.path.join(self.repo, "sources"), exist_ok=True)
        os.makedirs(os.path.join(self.repo, "wiki"), exist_ok=True)
        for fn, seed in (("index.md", "# Index\n\n"), ("log.md", "# Log\n\n")):
            p = os.path.join(self.repo, fn)
            if not os.path.exists(p):
                with open(p, "w") as f:
                    f.write(seed)
        if not os.path.isdir(os.path.join(self.repo, ".git")):
            self._git("init", "-q")
            self._git("config", "user.email", "llmwiki@localhost")
            self._git("config", "user.name", "llmwiki")
            self._git("add", "-A")
            self._git("commit", "-q", "-m", "init collection")

    def write_document(self, doc_id: str, source_text: str, wiki_text: str,
                       log_line: str) -> str:
        with open(os.path.join(self.repo, "sources", f"{doc_id}.txt"), "w") as f:
            f.write(source_text)
        with open(os.path.join(self.repo, "wiki", f"{doc_id}.md"), "w") as f:
            f.write(wiki_text)
        with open(os.path.join(self.repo, "log.md"), "a") as f:
            f.write(log_line.rstrip() + "\n")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", log_line)
        return self._git("rev-parse", "HEAD")
