import re
from pathlib import Path

import pytest

from app.models import ROLE_CUSTOMER

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"
TITLE_BLOCK = re.compile(r"{%\s*block title\s*%}(.*?){%\s*endblock\s*%}", re.DOTALL)

# The base page is the fallback and the home page is the landing page, so both lead with the name.
OWN_FORMAT = {"base.html", "home.html"}


def title_blocks():
    for path in sorted(TEMPLATES.rglob("*.html")):
        match = TITLE_BLOCK.search(path.read_text())
        if match and path.name not in OWN_FORMAT:
            yield path.relative_to(TEMPLATES).as_posix(), match.group(1).strip()


def test_there_are_title_blocks_to_check():
    # Guards the guard: if the pattern stopped matching, the next test would pass for nothing.
    assert len(list(title_blocks())) > 20


@pytest.mark.parametrize("name, title", list(title_blocks()))
def test_every_page_title_ends_with_a_pipe_and_the_site_name(name, title):
    # One format everywhere ("Page | QuickCraft"), so browser tabs and history read the same.
    assert title.endswith(" | QuickCraft"), f"{name}: {title!r}"


def test_tradespeople_search_shows_the_pipe_format_in_the_real_page(client, make_user, login):
    make_user(email="anna@example.com", role=ROLE_CUSTOMER, email_verified=True)
    login("anna@example.com")

    page = client.get("/reviews/tradespeople").get_data(as_text=True)

    assert "<title>Tradespeople | QuickCraft</title>" in page
