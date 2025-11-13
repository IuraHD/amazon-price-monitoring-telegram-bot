"""
Analytics module for tracking user navigation patterns and feature adoption.

This module provides functions to:
- Track navigation events (button clicks vs shortcuts)
- Calculate adoption rates for number-based shortcuts
- Measure feature usage and user engagement
"""

from datetime import datetime
from typing import Optional
from app.core.db import execute, fetch_one, fetch_all


def track_navigation_event(
    chat_id: int,
    event_type: str,
    shortcut_used: bool,
    context: Optional[str] = None
) -> int:
    """
    Track a navigation event for analytics.
    
    Args:
        chat_id: Telegram chat ID of the user
        event_type: Type of event (e.g., 'main_menu_add', 'product_view', 'folder_select')
        shortcut_used: True if user used number shortcut, False if clicked button
        context: Optional context information (e.g., 'main_menu', 'product_list')
    
    Returns:
        Event ID from database
    """
    return execute(
        """INSERT INTO navigation_events 
           (chat_id, event_type, shortcut_used, context, timestamp) 
           VALUES (?, ?, ?, ?, ?)""",
        (chat_id, event_type, int(shortcut_used), context, datetime.utcnow().isoformat())
    )


def get_shortcut_adoption_rate(days: int = 7, chat_id: Optional[int] = None) -> float:
    """
    Calculate percentage of actions using shortcuts vs buttons.
    
    Args:
        days: Number of days to look back (default: 7)
        chat_id: Optional filter for specific user
    
    Returns:
        Adoption rate as percentage (0.0 to 100.0)
    """
    query = """
        SELECT 
            COUNT(CASE WHEN shortcut_used = 1 THEN 1 END) * 100.0 / COUNT(*) as adoption_rate
        FROM navigation_events 
        WHERE timestamp > datetime('now', '-{} days')
    """.format(days)
    
    params = []
    if chat_id:
        query += " AND chat_id = ?"
        params.append(chat_id)
    
    result = fetch_one(query, tuple(params))
    return result['adoption_rate'] if result and result['adoption_rate'] else 0.0


def get_most_used_shortcuts(limit: int = 10, days: int = 7) -> list:
    """
    Get list of most frequently used shortcuts.
    
    Args:
        limit: Maximum number of results to return
        days: Number of days to look back
    
    Returns:
        List of dicts with event_type and usage_count
    """
    results = fetch_all(
        """SELECT 
               event_type,
               COUNT(*) as usage_count
           FROM navigation_events
           WHERE shortcut_used = 1 
           AND timestamp > datetime('now', '-{} days')
           GROUP BY event_type
           ORDER BY usage_count DESC
           LIMIT ?""".format(days),
        (limit,)
    )
    return [dict(row) for row in results]


def get_user_engagement_stats(chat_id: int, days: int = 7) -> dict:
    """
    Get engagement statistics for a specific user.
    
    Args:
        chat_id: Telegram chat ID
        days: Number of days to look back
    
    Returns:
        Dict with total_actions, shortcut_rate, avg_actions_per_day
    """
    result = fetch_one(
        """SELECT 
               COUNT(*) as total_actions,
               AVG(CASE WHEN shortcut_used = 1 THEN 1 ELSE 0 END) * 100 as shortcut_rate,
               COUNT(*) * 1.0 / ? as avg_actions_per_day
           FROM navigation_events
           WHERE chat_id = ?
           AND timestamp > datetime('now', '-{} days')""".format(days),
        (days, chat_id)
    )
    
    if result:
        return {
            'total_actions': result['total_actions'],
            'shortcut_rate': result['shortcut_rate'] or 0.0,
            'avg_actions_per_day': result['avg_actions_per_day'] or 0.0
        }
    return {'total_actions': 0, 'shortcut_rate': 0.0, 'avg_actions_per_day': 0.0}


def get_adoption_rate_by_day(days: int = 7) -> list:
    """
    Get daily adoption rate trend.
    
    Args:
        days: Number of days to look back
    
    Returns:
        List of dicts with date and adoption_rate
    """
    results = fetch_all(
        """SELECT 
               DATE(timestamp) as date,
               COUNT(CASE WHEN shortcut_used = 1 THEN 1 END) * 100.0 / COUNT(*) as adoption_rate,
               COUNT(*) as total_events
           FROM navigation_events
           WHERE timestamp > datetime('now', '-{} days')
           GROUP BY DATE(timestamp)
           ORDER BY date""".format(days)
    )
    return [dict(row) for row in results]


def get_response_time_stats(days: int = 7) -> dict:
    """
    Get response time statistics for navigation events.
    
    Args:
        days: Number of days to look back
    
    Returns:
        Dict with avg_response_time, p50, p95, p99 in milliseconds
    """
    # Note: This requires response_time_ms column to be populated
    # For now, returns placeholder values until response time tracking is implemented
    result = fetch_one(
        """SELECT 
               AVG(response_time_ms) as avg_time,
               COUNT(*) as total_events
           FROM navigation_events
           WHERE timestamp > datetime('now', '-{} days')
           AND response_time_ms IS NOT NULL""".format(days)
    )
    
    if result and result['total_events']:
        return {
            'avg_response_time': result['avg_time'],
            'events_measured': result['total_events']
        }
    return {'avg_response_time': None, 'events_measured': 0}


def get_error_rate(days: int = 7) -> float:
    """
    Calculate percentage of navigation events that resulted in errors.
    
    Args:
        days: Number of days to look back
    
    Returns:
        Error rate as percentage (0.0 to 100.0)
    """
    result = fetch_one(
        """SELECT 
               COUNT(CASE WHEN event_type LIKE '%_error' THEN 1 END) * 100.0 / COUNT(*) as error_rate
           FROM navigation_events
           WHERE timestamp > datetime('now', '-{} days')""".format(days)
    )
    return result['error_rate'] if result and result['error_rate'] else 0.0


def get_context_usage_stats(days: int = 7) -> list:
    """
    Get usage statistics broken down by navigation context.
    
    Args:
        days: Number of days to look back
    
    Returns:
        List of dicts with context, event_count, shortcut_rate
    """
    results = fetch_all(
        """SELECT 
               context,
               COUNT(*) as event_count,
               COUNT(CASE WHEN shortcut_used = 1 THEN 1 END) * 100.0 / COUNT(*) as shortcut_rate
           FROM navigation_events
           WHERE timestamp > datetime('now', '-{} days')
           AND context IS NOT NULL
           GROUP BY context
           ORDER BY event_count DESC""".format(days)
    )
    return [dict(row) for row in results]


def track_error_event(chat_id: int, error_type: str, context: Optional[str] = None) -> int:
    """
    Track an error event for monitoring.
    
    Args:
        chat_id: Telegram chat ID
        error_type: Type of error (e.g., 'invalid_shortcut', 'out_of_range')
        context: Optional context information
    
    Returns:
        Event ID from database
    """
    return track_navigation_event(
        chat_id=chat_id,
        event_type=f"{error_type}_error",
        shortcut_used=False,
        context=context
    )
