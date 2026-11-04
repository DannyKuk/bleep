from bleep.knowledge.chunking import chunk_markdown, estimate_tokens

PAGE = """# Restart policies

Docker can restart a container when it exits.

## Use a restart policy

Pass `--restart` to `docker run`.

### on-failure

Restarts only on a non-zero exit code.

## Restart policy details

A policy takes effect after the container ran for ten seconds.
"""


def letters(n: int) -> str:
    """`n` as a word of letters, so each word is one estimated token."""
    word = ""
    while True:
        n, rest = divmod(n, 26)
        word = "abcdefghijklmnopqrstuvwxyz"[rest] + word
        if n == 0:
            return word


def paragraph(word: str, words: int) -> str:
    """One paragraph of `words` one-token words over lines of ten, as the docs wrap them."""
    tokens = [f"{word}{letters(i)}" for i in range(words)]
    return "\n".join(" ".join(tokens[i : i + 10]) for i in range(0, words, 10))


def test_every_chunk_carries_the_path_of_headings_above_it() -> None:
    chunks = chunk_markdown(PAGE)

    assert [c.heading_path for c in chunks] == [
        ("Restart policies",),
        ("Restart policies", "Use a restart policy"),
        ("Restart policies", "Use a restart policy", "on-failure"),
        ("Restart policies", "Restart policy details"),
    ]
    assert chunks[2].text == "Restarts only on a non-zero exit code."


def test_a_sibling_heading_replaces_the_deeper_ones() -> None:
    text = "# A\n\n## B\n\n### C\n\nc\n\n## D\n\nd\n\n#### E\n\ne\n"

    paths = [c.heading_path for c in chunk_markdown(text)]

    assert paths == [("A", "B", "C"), ("A", "D"), ("A", "D", "E")]


def test_a_hash_line_inside_fenced_code_is_not_a_heading() -> None:
    text = "# Env\n\n## env_file\n\n```bash\n# Set Rails/Rack environment\nRACK_ENV=dev\n```\n"

    [chunk] = chunk_markdown(text)

    assert chunk.heading_path == ("Env", "env_file")
    assert "# Set Rails/Rack environment" in chunk.text


def test_text_before_the_first_heading_has_an_empty_path() -> None:
    [chunk, _] = chunk_markdown("Read me first.\n\n# Title\n\nbody\n")

    assert chunk.heading_path == ()
    assert chunk.text == "Read me first."


def test_headings_without_text_of_their_own_make_no_chunk() -> None:
    chunks = chunk_markdown("# A\n\n## B\n\n## C\n\nonly here\n")

    assert [c.heading_path for c in chunks] == [("A", "C")]


def test_heading_markup_is_dropped_from_the_path() -> None:
    [chunk] = chunk_markdown("# Services\n\n### `depends_on` {#depends-on}\n\ntext\n")

    assert chunk.heading_path == ("Services", "depends_on")


def test_a_long_section_is_split_under_the_limit_with_overlap() -> None:
    body = "\n\n".join(paragraph(f"p{letters(n)}w", 120) for n in range(5))  # 600 words
    chunks = chunk_markdown(f"# Big\n\n{body}\n", max_tokens=200, overlap=30)

    assert len(chunks) >= 3
    assert all(estimate_tokens(c.text) <= 200 for c in chunks)
    assert all(c.heading_path == ("Big",) for c in chunks)
    for before, after in zip(chunks, chunks[1:], strict=False):
        tail = before.text.split("\n")[-1]
        assert after.text.startswith(tail) or tail in after.text.split("\n")[:4]
    words = {w for c in chunks for w in c.text.split()}
    assert {f"p{letters(n)}w{letters(i)}" for n in range(5) for i in range(120)} <= words


def test_a_split_prefers_the_end_of_a_paragraph() -> None:
    text = f"# T\n\n{paragraph('a', 150)}\n\n{paragraph('b', 150)}\n"

    first, second = chunk_markdown(text, max_tokens=200, overlap=0)

    assert first.text == paragraph("a", 150)
    assert second.text == paragraph("b", 150)


def test_one_line_longer_than_the_limit_is_cut_into_pieces() -> None:
    line = " ".join(f"w{letters(i)}" for i in range(450))

    chunks = chunk_markdown(f"# T\n\n{line}\n", max_tokens=100, overlap=0)

    assert all(estimate_tokens(c.text) <= 100 for c in chunks)
    assert " ".join(c.text for c in chunks).split() == line.split()


def test_table_padding_is_squeezed_out() -> None:
    text = (
        "# T\n\n| Flag     | Description        |\n| :------- | :----------------- |\n"
        "| `no`     | Don't restart.     |\n"
    )

    [chunk] = chunk_markdown(text)

    assert chunk.text == "| Flag | Description |\n| --- | --- |\n| `no` | Don't restart. |"


def test_overlap_never_pushes_a_chunk_over_the_limit() -> None:
    lines = "\n".join(" ".join(f"x{letters(n)}y{letters(i)}" for i in range(45)) for n in range(10))

    chunks = chunk_markdown(f"# T\n\n{lines}\n", max_tokens=100, overlap=60)

    assert all(estimate_tokens(c.text) <= 100 for c in chunks)


def test_numbers_and_snake_case_count_as_the_model_splits_them() -> None:
    assert estimate_tokens("restart") == 1
    assert estimate_tokens("total_inactive_anon") == 5
    assert estimate_tokens("2017-07-12") == 6
    assert estimate_tokens("1234567") == 3


def test_empty_text_has_no_chunks() -> None:
    assert chunk_markdown("") == []
    assert chunk_markdown("# Only a heading\n") == []


def test_a_word_longer_than_the_limit_is_cut_by_characters():
    url = "https://example.com/" + "/".join(f"seg{i}" for i in range(500))

    chunks = chunk_markdown(f"# T\n\nsee {url} now\n", max_tokens=100, overlap=0)

    assert all(estimate_tokens(c.text) <= 100 for c in chunks)
    assert "".join(c.text for c in chunks).replace(" ", "").replace("\n", "") == f"see{url}now"
