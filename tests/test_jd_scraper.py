from resume_tailor.collector.jd_scraper import extract_jd_html


def test_extract_jd_from_html():
    html = """
    <html><body>
      <header>nav junk</header>
      <article>
        <h1>Senior Backend Engineer</h1>
        <p>We are hiring a Senior Backend Engineer to build distributed systems.</p>
        <ul><li>Redis</li><li>PostgreSQL</li><li>Python</li></ul>
      </article>
      <footer>footer junk</footer>
    </body></html>
    """
    text = extract_jd_html(html)
    assert "Senior Backend Engineer" in text
    assert "Redis" in text
    assert "PostgreSQL" in text


def test_extract_empty_html():
    assert extract_jd_html("<html></html>") == ""
    assert extract_jd_html("") == ""
