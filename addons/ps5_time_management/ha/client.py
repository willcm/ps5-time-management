"""Home Assistant REST API client"""
import json
import logging
import os
from datetime import datetime, timedelta
from urllib.request import Request, urlopen, HTTPError
from urllib.parse import quote

logger = logging.getLogger(__name__)


class HomeAssistantClient:
    """Client for interacting with Home Assistant REST API"""
    
    def __init__(self, base_url=None, token=None):
        """Initialize HA client
        
        Args:
            base_url: Home Assistant URL (default: http://supervisor or http://homeassistant:8123)
            token: Long-lived access token (from HA profile)
        """
        # Try to get from environment (add-on context)
        self.base_url = base_url or os.environ.get('SUPERVISOR_API', 'http://supervisor')
        self.supervisor_token = token or os.environ.get('SUPERVISOR_TOKEN') or os.environ.get('HA_TOKEN')
        
        # When using supervisor proxy to Home Assistant Core API
        # Need homeassistant_api: true in config.yaml and SUPERVISOR_TOKEN as bearer token
        if self.base_url == 'http://supervisor' or 'supervisor' in self.base_url:
            # Use /core/api endpoint - Supervisor proxies to Home Assistant Core API
            self.api_base = f"{self.base_url}/core/api"
            # Must use SUPERVISOR_TOKEN as bearer token per HA docs
            if not self.supervisor_token:
                logger.error("SUPERVISOR_TOKEN required for Home Assistant Core API access")
        else:
            # Direct HA URL (e.g., http://homeassistant:8123) - requires long-lived access token
            if not self.base_url.startswith('http'):
                self.base_url = f"http://{self.base_url}"
            self.api_base = f"{self.base_url}/api"
        
        # Set headers - SUPERVISOR_TOKEN is required as bearer token for /core/api
        if self.supervisor_token:
            self.headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {self.supervisor_token}'
            }
            logger.debug("Using SUPERVISOR_TOKEN for Home Assistant Core API access")
        else:
            # No token available
            self.headers = {
                'Content-Type': 'application/json'
            }
            logger.error("SUPERVISOR_TOKEN not available - Home Assistant Core API access will fail")
        
        logger.info(f"Initialized HA client: api_base={self.api_base}, has_token={bool(self.supervisor_token)}")
    
    def _request(self, method, endpoint, data=None):
        """Make HTTP request to HA API"""
        url = f"{self.api_base}/{endpoint.lstrip('/')}"
        
        try:
            req_data = None
            if data:
                req_data = json.dumps(data).encode('utf-8')
            
            req = Request(url, data=req_data, headers=self.headers, method=method)
            
            with urlopen(req, timeout=10) as response:
                if response.status == 200 or response.status == 201:
                    return json.loads(response.read().decode('utf-8'))
                else:
                    logger.error(f"HA API error: {response.status} - {response.read().decode('utf-8')}")
                    return None
        except HTTPError as e:
            error_body = None
            try:
                error_body = e.read().decode('utf-8') if hasattr(e, 'read') else str(e.reason)
            except:
                pass
            
            if e.code == 401:
                # For supervisor /core/api, need homeassistant_api: true and SUPERVISOR_TOKEN
                if '/core/api' in self.api_base:
                    logger.error(f"HA API authentication failed (401): {error_body}. Ensure homeassistant_api: true is set in config.yaml and SUPERVISOR_TOKEN is available.")
                else:
                    logger.error(f"HA API authentication failed (401): {error_body}. Check SUPERVISOR_TOKEN availability.")
            elif e.code == 404:
                logger.debug(f"HA API endpoint not found: {endpoint}")
            else:
                logger.warning(f"HA API HTTP error {e.code}: {error_body or e.reason}")
            return None
        except Exception as e:
            logger.warning(f"HA API request failed: {e}")
            return None
    
    def get_state(self, entity_id):
        """Get current state of an entity"""
        return self._request('GET', f'states/{quote(entity_id, safe="")}')
    
    def get_history(self, entity_id, start_time=None, end_time=None, significant_changes_only=True):
        """Get state history for an entity
        
        Args:
            entity_id: Entity ID (e.g., 'sensor.ps5_john_game')
            start_time: Start datetime (default: 24 hours ago)
            end_time: End datetime (default: now)
            significant_changes_only: Only return significant state changes
        
        Returns:
            List of state changes, or None on error
        """
        if not start_time:
            start_time = datetime.now() - timedelta(days=1)
        if not end_time:
            end_time = datetime.now()
        
        # HA API expects ISO format timestamps
        start_iso = start_time.isoformat()
        end_iso = end_time.isoformat()
        
        endpoint = f'history/period/{start_iso}'
        if entity_id:
            endpoint += f'?filter_entity_id={quote(entity_id, safe="")}'
            if significant_changes_only:
                endpoint += '&significant_changes_only=1'
        
        result = self._request('GET', endpoint)
        
        if result and len(result) > 0:
            # Result is a list of lists (one per entity)
            # Each entity's history is a list of state changes
            entity_history = None
            for entity_list in result:
                if entity_list and len(entity_list) > 0:
                    if entity_list[0].get('entity_id') == entity_id:
                        entity_history = entity_list
                        break
            
            return entity_history
        return None
    
    def is_available(self):
        """Check if HA API is available"""
        if not self.supervisor_token:
            logger.debug("No SUPERVISOR_TOKEN available, skipping HA API check")
            return False
        try:
            # Try to get any entity state to verify API access
            # Use a common entity that should always exist
            state = self.get_state('sensor.time')
            if state is None:
                # Try alternative check
                result = self._request('GET', 'config')
                return result is not None
            return True
        except Exception as e:
            logger.debug(f"HA API availability check failed: {e}")
            return False

