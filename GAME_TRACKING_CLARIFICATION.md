# Game Tracking Clarification

## Current State

### What's Working ✅
1. **Total Time Tracking**: `get_user_time_today()`, `get_user_weekly_time()`, `get_user_monthly_time()`
   - ✅ Updated to use HA history (from `binary_sensor.ps5_{user}_session_active`)
   - ✅ Falls back to SQLite if HA unavailable

### What's NOT Updated Yet ❌
2. **Per-Game Time Tracking**: These methods still use SQLite:
   - `get_game_time_today(user, game)` - Currently queries SQLite `game_stats` table
   - `get_game_time_weekly(user, game)` - Currently queries SQLite `game_stats` table  
   - `get_game_time_monthly(user, game)` - Currently queries SQLite `game_stats` table
   - `get_top_games(user, days, limit)` - Currently queries SQLite `game_stats` table
   - `get_all_games_stats(user)` - Currently queries SQLite `game_stats` table

## What I Meant

**Current (SQLite-based):**
```python
def get_game_time_today(self, user, game):
    # Queries SQLite game_stats table
    c.execute('SELECT SUM(minutes_played) FROM game_stats 
               WHERE user=? AND game=? AND date=?', ...)
```

**Proposed (HA History-based):**
```python
def get_game_time_today(self, user, game):
    if self._is_ha_available():
        # Query HA history for sensor.ps5_{user}_game
        # Calculate time "F1 2024" was the state
        from ha.history import get_game_times_from_ha
        game_times = get_game_times_from_ha(client, user, today, today)
        return game_times.get(game, 0)
    else:
        # Fallback to SQLite
```

## The Question

Should I update these game tracking methods to:
1. **Use HA history** as the source of truth (query `sensor.ps5_{user}_game` state transitions)
2. **Fall back to SQLite** if HA unavailable
3. This would make HA the single source of truth for game times too, not just total time

## Example

**Current Flow:**
- Game session ends → `end_session()` saves to SQLite `game_stats` table
- `get_game_time_today()` queries SQLite to get game time

**Proposed Flow:**
- Game state changes → `sensor.ps5_john_game` = "F1 2024" (tracked by HA automatically)
- `get_game_time_today()` queries HA history to calculate how long "F1 2024" was the state
- SQLite `game_stats` becomes backup/cache only

## Benefits

1. ✅ **Single Source of Truth**: HA history for all time tracking (total + per-game)
2. ✅ **More Accurate**: No risk of SQLite/HA mismatch
3. ✅ **Simpler Code**: No need to maintain `game_stats` table (except as cache)
4. ✅ **Consistent**: Same approach for total time and per-game time

## Impact

- **API endpoints** would automatically use HA history (no changes needed)
- **Web UI** would show accurate game times from HA
- **SQLite `game_stats`** table becomes optional/backup
- **Migration**: Existing SQLite data remains as backup

Would you like me to:
- **A)** Update all game tracking methods to use HA history (recommended)
- **B)** Leave them as-is (using SQLite)
- **C)** Hybrid approach (use HA when available, SQLite as primary)

