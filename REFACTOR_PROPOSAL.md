# main.py Refactoring Proposal

## Current State
- **Size**: 2,141 lines, 88KB
- **Structure**: Monolithic single file with everything mixed together

## Unused Code (Can Be Removed)
1. **`/test` route** (lines 1884-1913) - Debug/test route, not referenced anywhere
2. **`/user-management` route** (lines 1915-1923) - Superseded by unified home page at `/`
3. **`/api/users/view` route** (lines 1462-1491) - Raw HTML output, replaced by modern UI

**Total removable**: ~60 lines

## Proposed Modular Structure

### 1. **`models.py`** (~600 lines)
- `PS5TimeManager` class
- All database operations
- User/stats/game data access methods

### 2. **`mqtt_handler.py`** (~450 lines)
- MQTT client setup
- `on_connect`, `on_message` handlers
- Device status updates
- Sensor publishing
- Discovery logic

### 3. **`shutdown_manager.py`** (~150 lines)
- `log_shutdown_event()`
- `has_shutdown_today()`
- `apply_shutdown_policy()`
- `start_shutdown_warning()`
- `enforce_standby()`

### 4. **`routes/api.py`** (~600 lines)
- All `/api/*` JSON endpoints
- User stats, game stats, limits, etc.

### 5. **`routes/web.py`** (~150 lines)
- `/` - Home page
- `/stats/<user>` - Stats page
- `/globals.css` - CSS route
- `/ps5.svg` - Icon route

### 6. **`routes/static.py`** (~100 lines)
- Image serving routes
- Static file handling

### 7. **`config.py`** (~100 lines)
- `load_config()`
- `get_mqtt_config()`
- `setup_logging()`

### 8. **`main.py`** (~200 lines)
- Flask app initialization
- `main()` entry point
- Route registration
- Thread management

## Benefits
1. **Maintainability**: Easier to find and modify specific functionality
2. **Testability**: Individual modules can be tested independently
3. **Readability**: Smaller, focused files
4. **Collaboration**: Multiple developers can work on different modules
5. **Reusability**: Components can be reused or extracted

## Migration Strategy
1. Create module structure
2. Move code incrementally (one module at a time)
3. Test after each migration
4. Remove unused routes
5. Update imports

## Estimated Impact
- **Reduction**: From 2,141 lines to ~200-600 lines per file
- **Removal**: ~60 lines of unused code
- **Organization**: Clear separation of concerns

