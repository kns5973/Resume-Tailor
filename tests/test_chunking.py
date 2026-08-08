from resume_tailor.collector.chunking import chunk_text


def test_empty_input():
    assert chunk_text("") == []
    assert chunk_text("   \n  ") == []


def test_short_text_single_chunk():
    chunks = chunk_text("hello world")
    assert len(chunks) == 1
    assert "hello world" in chunks[0]


def test_long_text_splits_with_overlap():
    text = "\n".join(f"line {i:03d} " + "x" * 40 for i in range(60))
    # lines are 48 chars; overlap must exceed one line to actually carry it
    chunks = chunk_text(text, chunk_size=200, overlap=100)
    assert len(chunks) >= 2
    # overlap: chunk i's boundary line is carried into chunk i+1
    assert chunks[0].splitlines()[-1] in chunks[1]


def test_long_line_stays_whole():
    line = "y" * 5000
    chunks = chunk_text(line, chunk_size=100)
    assert chunks == [line]
