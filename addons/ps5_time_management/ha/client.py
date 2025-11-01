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
        # Try supervisor first, then fallback to homeassistant:8123
        if self.base_url == 'http://supervisor':
            # In add-on context, supervisor provides HA API at /homeassistant/api
            self.api_base = f"{self.base_url}/homeassistant/api"
        elif 'supervisor' in self.base_url:
            # Alternative supervisor URL format
            self.api_base = f"{self.base_url}/homeassistant/api"
        else:
            # Direct HA URL (e.g., http://homeassistant:8123)
            if not self.base_url.startswith('http'):
                self.base_url = f"http://{self.base_url}"
            self.api_base = f"{self.base_url}/api"
        
        self.token = token or os.environ.get('SUPERVISOR_TOKEN') or os.environ.get('HA_TOKEN')
        self.headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.token}' if self.token else None
        }
        # Remove None headers
        self.headers = {k: v for k, v in self.headers.items() if v is not None}
        
        logger.info(f"Initialized HA client: base_url={self.api_base}, has_token={bool(self.token)}")
    
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
            if e.code == 404:
                logger.debug(f"HA API endpoint not found: {endpoint}")
            else:
                logger.warning(f"HA API HTTP error {e.code}: {e.reason}")
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
        try:
            state = self.get_state('sensor.time')
            return state is not None
        except Exception:
            return False

