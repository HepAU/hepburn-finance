"""Send notifications via the Home Assistant Supervisor API.

Add-ons running inside HA can call services through the Supervisor's proxy
without needing a long-lived token. We use the SUPERVISOR_TOKEN env var
which HA injects automatically.
"""
import os
import json
import logging
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

SUPERVISOR_URL = 'http://supervisor/core/api'
TOKEN = os.environ.get('SUPERVISOR_TOKEN', '')


def call_ha_service(domain, service, data=None):
    """Invoke a HA service via the supervisor proxy."""
    if not TOKEN:
        logger.warning('No SUPERVISOR_TOKEN — skipping HA service call')
        return False

    url = f'{SUPERVISOR_URL}/services/{domain}/{service}'
    headers = {
        'Authorization': f'Bearer {TOKEN}',
        'Content-Type': 'application/json',
    }
    body = json.dumps(data or {}).encode('utf-8')
    req = urllib.request.Request(url, data=body, headers=headers, method='POST')

    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status < 300
    except urllib.error.HTTPError as e:
        logger.error('HA service call failed (%s): %s', e.code, e.reason)
        return False
    except Exception as e:
        logger.error('HA service call error: %s', e)
        return False


def notify(title, message, target=None):
    """Send a notification through HA. Target = name of notify service
    (e.g. 'mobile_app_lukes_phone'). If None, uses persistent_notification
    in the HA frontend."""
    if target:
        domain = 'notify'
        service = target
        data = {'title': title, 'message': message}
    else:
        domain = 'persistent_notification'
        service = 'create'
        data = {'title': title, 'message': message,
                'notification_id': 'hepburn_finance'}
    return call_ha_service(domain, service, data)
