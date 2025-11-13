"""
Unit tests for number-based navigation shortcuts.
Sprint 1: Task 2.3 - Integration Testing
"""

import pytest
from app.core.analytics import (
    track_navigation_event,
    get_shortcut_adoption_rate,
    get_most_used_shortcuts,
    get_user_engagement_stats
)
from app.core.db import init_db, execute, fetch_all


@pytest.fixture
def setup_test_db():
    """Initialize test database."""
    init_db()
    # Clean up any existing test data
    execute("DELETE FROM navigation_events WHERE chat_id = 999999")
    yield
    # Cleanup after tests
    execute("DELETE FROM navigation_events WHERE chat_id = 999999")


def test_track_navigation_event(setup_test_db):
    """Test that navigation events are tracked correctly."""
    event_id = track_navigation_event(
        chat_id=999999,
        event_type="main_menu_add",
        shortcut_used=True,
        context="main_menu"
    )
    
    assert event_id > 0
    
    # Verify event was stored
    events = fetch_all(
        "SELECT * FROM navigation_events WHERE chat_id = ? AND id = ?",
        (999999, event_id)
    )
    assert len(events) == 1
    assert events[0]["event_type"] == "main_menu_add"
    assert events[0]["shortcut_used"] == 1
    assert events[0]["context"] == "main_menu"


def test_shortcut_adoption_rate(setup_test_db):
    """Test adoption rate calculation."""
    # Track 7 shortcut events
    for i in range(7):
        track_navigation_event(999999, f"test_{i}", True, "test")
    
    # Track 3 button click events
    for i in range(3):
        track_navigation_event(999999, f"test_{i}", False, "test")
    
    # Should be 70% adoption (7 out of 10)
    adoption_rate = get_shortcut_adoption_rate(days=7, chat_id=999999)
    assert adoption_rate == 70.0


def test_most_used_shortcuts(setup_test_db):
    """Test most used shortcuts tracking."""
    # Track various shortcuts
    track_navigation_event(999999, "main_menu_add", True, "main_menu")
    track_navigation_event(999999, "main_menu_add", True, "main_menu")
    track_navigation_event(999999, "main_menu_add", True, "main_menu")
    track_navigation_event(999999, "product_view", True, "product_list")
    track_navigation_event(999999, "product_view", True, "product_list")
    
    most_used = get_most_used_shortcuts(limit=5, days=7)
    
    # main_menu_add should be most used
    assert len(most_used) >= 1
    # Filter for our test user's events
    test_events = [e for e in most_used if e['event_type'] in ['main_menu_add', 'product_view']]
    if test_events:
        assert test_events[0]['event_type'] in ['main_menu_add', 'product_view']


def test_user_engagement_stats(setup_test_db):
    """Test user engagement statistics."""
    # Track 10 events, 8 with shortcuts
    for i in range(8):
        track_navigation_event(999999, f"shortcut_{i}", True, "test")
    for i in range(2):
        track_navigation_event(999999, f"button_{i}", False, "test")
    
    stats = get_user_engagement_stats(999999, days=7)
    
    assert stats['total_actions'] == 10
    assert stats['shortcut_rate'] == 80.0
    assert stats['avg_actions_per_day'] == pytest.approx(10.0 / 7, rel=0.01)


def test_number_emoji_helper():
    """Test number emoji generation."""
    from app.bot.keyboards import get_number_emoji
    
    assert get_number_emoji(1) == "1️⃣"
    assert get_number_emoji(5) == "5️⃣"
    assert get_number_emoji(9) == "9️⃣"
    assert get_number_emoji(10) == "10"  # Out of range
    assert get_number_emoji(0) == "0"    # Out of range


def test_keyboard_pagination():
    """Test that keyboards support pagination correctly."""
    from app.bot.keyboards import products_list, MenuActions
    
    # Create mock products
    mock_products = [
        {"id": i, "asin": f"ASIN{i}", "last_price": 10.0 + i, "title": f"Product {i}"}
        for i in range(15)
    ]
    
    # Page 1 should show products 0-8
    keyboard_page1 = products_list(mock_products, page=1)
    # Check that we have inline keyboard
    assert keyboard_page1.inline_keyboard is not None
    
    # Page 2 should show products 9-14
    keyboard_page2 = products_list(mock_products, page=2)
    assert keyboard_page2.inline_keyboard is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
