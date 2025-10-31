# Modular Refactoring Checklist

## Phase 1: Preparation & Backup ✅
- [x] Remove unused code (test route, user-management route, api/users/view route)
- [ ] Create git branch: `git checkout -b refactor/modular-structure`
- [ ] Verify all tests pass before starting
- [ ] Document current structure (file size, line count)
- [ ] Create backup branch: `git branch backup/pre-refactor`

## Phase 2: Create Module Structure
- [x] Create `models/` directory
  - [x] Create `models/__init__.py`
  - [ ] Create `models/time_manager.py` (PS5TimeManager class)
  - [ ] Create `models/database.py` (DB initialization & utilities)
  
- [x] Create `mqtt/` directory
  - [x] Create `mqtt/__init__.py`
  - [x] Create `mqtt/handler.py` (MQTT message handlers)
  - [x] Create `mqtt/discovery.py` (User discovery logic)
  - [x] Create `mqtt/sensors.py` (Sensor publishing)
  - [x] Extract `handle_device_update`, `handle_state_change`, etc. to handler.py
  - [x] Extract `publish_user_sensors`, `update_all_sensor_states`, etc. to sensors.py
  - [x] Update main.py to import from mqtt modules
  
- [x] Create `shutdown/` directory
  - [x] Create `shutdown/__init__.py`
  - [x] Create `shutdown/manager.py` (Shutdown policy & enforcement)
  
- [ ] Create `routes/` directory
  - [ ] Create `routes/__init__.py`
  - [ ] Create `routes/api.py` (All /api/* endpoints)
  - [ ] Create `routes/web.py` (HTML page routes)
  - [ ] Create `routes/static.py` (Static file serving)
  
- [x] Create `config/` directory
  - [x] Create `config/__init__.py`
  - [x] Create `config/loader.py` (Config loading)
  - [x] Create `config/mqtt_config.py` (MQTT config)
  - [x] Create `config/logging.py` (Logging setup)
  
- [x] Create `utils/` directory
  - [x] Create `utils/__init__.py`
  - [x] Create `utils/timers.py` (Timer checking)
  - [x] Create `utils/data_cleanup.py` (Data cleanup utilities)
  
- [x] Extract smaller utility functions
  - [x] Extract `discover_users_from_ps5_mqtt()` to `mqtt/discovery.py`
  - [x] Extract `check_timers()` to `utils/timers.py`
  - [x] Extract `clear_all_user_data()` to `utils/data_cleanup.py`
  - [x] Extract shutdown functions to `shutdown/manager.py`
  - [x] Update main.py to import from modules

## Phase 3: Extract Models Module
- [ ] Move `PS5TimeManager` class to `models/time_manager.py`
  - [ ] Copy class definition (lines ~160-833)
  - [ ] Update imports in class
  - [ ] Test import: `from models.time_manager import PS5TimeManager`
  
- [ ] Move database initialization to `models/database.py`
  - [ ] Extract `init_database()` method
  - [ ] Extract table creation logic
  - [ ] Test database initialization
  
- [ ] Update `main.py` to import from models
  - [ ] `from models.time_manager import PS5TimeManager`
  - [ ] Verify `time_manager` instance still works
  - [ ] Test all database operations

## Phase 4: Extract MQTT Module
- [ ] Move MQTT client setup to `mqtt/handler.py`
  - [ ] Extract `on_connect()` function (lines ~863-891)
  - [ ] Extract `on_message()` function (lines ~894-926)
  - [ ] Extract MQTT client initialization
  - [ ] Export `mqtt_client` for use in other modules
  
- [ ] Move message handlers to `mqtt/handler.py`
  - [ ] Extract `handle_device_update()` (lines ~928-1025)
  - [ ] Extract `handle_state_change()`, `handle_game_change()`, etc.
  - [ ] Extract `discover_users_from_ps5_mqtt()` (lines ~834-862)
  
- [ ] Move sensor publishing to `mqtt/sensors.py`
  - [ ] Extract `publish_user_sensors()` (lines ~1027-1126)
  - [ ] Extract `update_user_sensor_states()` (lines ~1132-1194)
  - [ ] Extract `update_all_sensor_states()` (lines ~1127-1131)
  
- [ ] Update `main.py` to import from mqtt
  - [ ] Import mqtt handlers
  - [ ] Register callbacks
  - [ ] Test MQTT connectivity

## Phase 5: Extract Shutdown Module
- [ ] Move shutdown functions to `shutdown/manager.py`
  - [ ] Extract `log_shutdown_event()` (lines ~78-87)
  - [ ] Extract `has_shutdown_today()` (lines ~89-103)
  - [ ] Extract `apply_shutdown_policy()` (lines ~105-111)
  - [ ] Extract `start_shutdown_warning()` (lines ~113-136)
  - [ ] Extract `enforce_standby()` (lines ~138-151)
  
- [ ] Update imports in main.py
  - [ ] `from shutdown.manager import *`
  - [ ] Verify shutdown logic still works
  - [ ] Test shutdown enforcement

## Phase 6: Extract Routes Module
- [ ] Move API routes to `routes/api.py`
  - [ ] Extract all `/api/*` routes (~600 lines)
  - [ ] Group by functionality (users, stats, games, limits, etc.)
  - [ ] Create blueprint: `api_bp = Blueprint('api', __name__, url_prefix='/api')`
  
- [ ] Move web routes to `routes/web.py`
  - [ ] Extract `/` route (home page)
  - [ ] Extract `/stats/<user>` route
  - [ ] Extract `/globals.css` route
  - [ ] Create blueprint: `web_bp = Blueprint('web', __name__)`
  
- [ ] Move static routes to `routes/static.py`
  - [ ] Extract `/images/<filename>` route
  - [ ] Extract `/stats/<user>/image/<filename>` route
  - [ ] Extract `/ps5.svg` route
  - [ ] Extract `/api/images` route
  - [ ] Create blueprint: `static_bp = Blueprint('static', __name__)`
  
- [ ] Update `main.py` to register blueprints
  - [ ] Import blueprints
  - [ ] `app.register_blueprint(api_bp)`
  - [ ] `app.register_blueprint(web_bp)`
  - [ ] `app.register_blueprint(static_bp)`
  - [ ] Test all routes

## Phase 7: Extract Config Module
- [ ] Move config loading to `config/loader.py`
  - [ ] Extract `load_config()` function (lines ~1960-1991)
  - [ ] Extract `clear_all_user_data()` if used during config load
  
- [ ] Move MQTT config to `config/mqtt_config.py`
  - [ ] Extract `get_mqtt_config()` function (lines ~1993-2063)
  - [ ] Handle HA supervisor config
  - [ ] Handle manual config
  
- [ ] Move logging setup to `config/logging.py`
  - [ ] Extract `setup_logging()` function (lines ~23-47)
  - [ ] Keep in config module as it's setup code
  
- [ ] Update `main.py` imports
  - [ ] `from config.loader import load_config`
  - [ ] `from config.mqtt_config import get_mqtt_config`
  - [ ] `from config.logging import setup_logging`

## Phase 8: Refactor main.py
- [ ] Clean up main.py to be minimal entry point
  - [ ] Keep only Flask app initialization
  - [ ] Keep `main()` function
  - [ ] Keep global variables that need to be shared
  - [ ] Import all modules
  
- [ ] Organize global state
  - [ ] Move to `config/state.py` or keep in main if needed
  - [ ] Document shared state (discovered_users, latest_device_status, etc.)
  
- [ ] Update imports
  - [ ] Remove moved functions
  - [ ] Add module imports
  - [ ] Verify all imports resolve

## Phase 9: Testing & Verification
- [ ] **Functional Testing**
  - [ ] Test MQTT connection
  - [ ] Test user discovery
  - [ ] Test session tracking
  - [ ] Test time limits enforcement
  - [ ] Test shutdown warnings
  - [ ] Test all API endpoints
  - [ ] Test web pages render correctly
  - [ ] Test static file serving
  
- [ ] **Integration Testing**
  - [ ] Verify Home Assistant integration works
  - [ ] Test sensor discovery
  - [ ] Test sensor state updates
  - [ ] Test ingress routing
  
- [ ] **Code Quality**
  - [ ] Run linter (if available)
  - [ ] Check for unused imports
  - [ ] Verify no circular dependencies
  - [ ] Check file sizes (aim for < 600 lines per file)

## Phase 10: Documentation & Cleanup
- [ ] Update README.md with new structure
- [ ] Add docstrings to all modules
- [ ] Create `ARCHITECTURE.md` documenting module structure
- [ ] Update any developer documentation
- [ ] Verify all comments are still relevant

## Phase 11: Final Verification
- [ ] Run full test suite (if exists)
- [ ] Manual testing checklist:
  - [ ] Add-on starts successfully
  - [ ] Home page loads
  - [ ] Stats pages work
  - [ ] User management works
  - [ ] Time limits enforced
  - [ ] Shutdown warnings work
  - [ ] MQTT sensors published
  - [ ] Game images cache
  - [ ] API endpoints respond
  
- [ ] Performance check
  - [ ] No significant performance regression
  - [ ] Memory usage acceptable
  - [ ] Startup time acceptable

## Phase 12: Deployment
- [ ] Review all changes: `git diff backup/pre-refactor`
- [ ] Update version in config.yaml
- [ ] Commit changes with descriptive message
- [ ] Test in staging/development environment
- [ ] Create PR or merge to main
- [ ] Tag release

## Module Dependency Map
```
main.py
├── config/
│   ├── loader.py (load_config)
│   ├── mqtt_config.py (get_mqtt_config)
│   └── logging.py (setup_logging)
├── models/
│   ├── time_manager.py (PS5TimeManager)
│   └── database.py (init_database)
├── mqtt/
│   ├── handler.py (on_connect, on_message, handle_device_update)
│   ├── discovery.py (discover_users_from_ps5_mqtt)
│   └── sensors.py (publish_user_sensors, update_user_sensor_states)
├── shutdown/
│   └── manager.py (all shutdown functions)
└── routes/
    ├── api.py (all /api/* endpoints)
    ├── web.py (/, /stats/<user>, /globals.css)
    └── static.py (image serving, SVG serving)
```

## Estimated File Sizes After Refactor
- `main.py`: ~200 lines (down from 2069)
- `models/time_manager.py`: ~400 lines
- `models/database.py`: ~200 lines
- `mqtt/handler.py`: ~250 lines
- `mqtt/discovery.py`: ~30 lines
- `mqtt/sensors.py`: ~150 lines
- `shutdown/manager.py`: ~80 lines
- `routes/api.py`: ~600 lines
- `routes/web.py`: ~100 lines
- `routes/static.py`: ~100 lines
- `config/loader.py`: ~50 lines
- `config/mqtt_config.py`: ~70 lines
- `config/logging.py`: ~30 lines

**Total**: ~2,060 lines (similar to original, but organized)

## Notes
- Work incrementally: complete one phase before moving to next
- Test after each phase
- Commit after each successful phase
- Use git to easily rollback if issues found
- Keep this checklist updated as you progress

