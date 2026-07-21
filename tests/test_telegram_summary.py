from telegram_summary import build_message_lines, compose_report, split_message


def _row(name, price, stock):
    return {"name": name, "price": price, "stock": stock}


def test_build_message_lines_flags_low_stock():
    rows = [_row("Товар А", "100", "2"), _row("Товар Б", "200", "50")]
    normal, low = build_message_lines(rows, threshold=5)
    assert len(low) == 1
    assert "Товар А" in low[0]
    assert "заканчивается" in low[0]
    assert len(normal) == 1
    assert "Товар Б" in normal[0]


def test_build_message_lines_boundary_is_exclusive():
    # остаток, равный порогу, не считается низким (< threshold, а не <=)
    rows = [_row("Товар В", "100", "5")]
    normal, low = build_message_lines(rows, threshold=5)
    assert len(low) == 0
    assert len(normal) == 1


def test_build_message_lines_handles_bad_stock_value():
    rows = [_row("Товар Г", "100", "n/a")]
    normal, low = build_message_lines(rows, threshold=5)
    assert len(low) == 1  # некорректный остаток трактуется как 0 -> ниже порога


def test_compose_report_contains_counts():
    rows = [_row("Товар А", "100", "2")]
    report = compose_report(rows, threshold=5)
    assert "Всего товаров: 1" in report
    assert "Заканчиваются (1)" in report


def test_split_message_respects_limit():
    text = "\n".join(f"строка {i}" for i in range(1000))
    chunks = split_message(text, limit=100)
    assert len(chunks) > 1
    assert all(len(c) <= 100 for c in chunks)
    # ничего не потеряно при разбиении
    assert "\n".join(chunks) == text


def test_split_message_single_chunk_when_short():
    assert split_message("короткий текст", limit=100) == ["короткий текст"]
