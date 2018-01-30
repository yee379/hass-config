"""
Support for Ubiquiti mFi sensors.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/sensor.mfi/
"""
import logging

import requests
import voluptuous as vol

from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.const import (
    STATE_ON, STATE_OFF, STATE_UNKNOWN,
    CONF_PASSWORD, CONF_USERNAME, TEMP_CELSIUS, CONF_HOST,
    CONF_VERIFY_SSL, CONF_SSL, CONF_PORT, CONF_TOKEN)
from homeassistant.helpers.entity import Entity
import homeassistant.helpers.config_validation as cv
from homeassistant.components.switch import SwitchDevice

_LOGGER = logging.getLogger(__name__)

DEFAULT_SSL = False
DEFAULT_VERIFY_SSL = False

DIGITS = {
    'volts': 1,
    'amps': 1,
    'active_power': 0,
    'temperature': 1,
}

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_HOST): cv.string,
    vol.Required(CONF_USERNAME): cv.string,
    vol.Required(CONF_PASSWORD): cv.string,
    vol.Required(CONF_TOKEN): cv.string,
    vol.Optional(CONF_PORT): cv.port,
    vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): cv.boolean,
    vol.Optional(CONF_SSL, default=DEFAULT_SSL): cv.boolean,
})


# pylint: disable=unused-variable
def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up mFi device sensors."""
    host = config.get(CONF_HOST)
    username = config.get(CONF_USERNAME)
    password = config.get(CONF_PASSWORD)
    ssl = config.get(CONF_SSL)
    verify_ssl = config.get(CONF_VERIFY_SSL)
    token = config.get(CONF_TOKEN)
    port_number = int(config.get(CONF_PORT, 443))

    api = MFiDevice( host, username, password, token, port_number=port_number, ssl=ssl, verify_ssl=verify_ssl)
    entities = []
    for p in api.ports():
        name = "%s_port%s" % (host,p['port'])
        _LOGGER.debug("Adding mfi device %s" % (name,))
        entities.append( MfiDeviceSensor(hass,api,p['port'],name) )
    add_devices(entities)


class MFiDevice(object):
    def __init__(self, host, username, password, token, port_number=18080, ssl=False, verify_ssl=False):
        self.host = host
        self.port_number = port_number
        self.username = username
        self.password = password
        self.cookies = {'AIROS_SESSIONID': token }
        self._verify_ssl = verify_ssl
        self.ssl = ssl
        
        self._timeout = 30
        
        self._login()
        self.data = None

    def _submit(self, method, url, parse_response_as_json=True, **extra):
        response = getattr(requests, method)( url, cookies=self.cookies, verify=self._verify_ssl, timeout=self._timeout, **extra )
        if not response.status_code == requests.codes.ok:
            _LOGGER.debug("STATUS %s: %s" % (response.status_code,response.content))
            response.raise_for_status()
        if parse_response_as_json:
            json = response.json()
            _LOGGER.debug("MFI %s: %s" % (self.host,json,))
            if not 'status' in json or not json['status'] == 'success' or not 'sensors' in json:
                raise Exception('Could not parse data for mfi device %s at %s' % (self.host,url))
            return json
        return response
        
    def _login(self):
        url = 'https://%s/login.cgi' % self.host
        response = self._submit('post', url, parse_response_as_json=False, data=[ ('username', self.username ), ( 'password', self.password ) ] )
        if b'POST Error' in response.content:
            _LOGGER.error("Error logging into %s" % self.host)
        
    def ports(self):
        url = '%s://%s:%s/sensors/' % ('https' if self.ssl else 'http', self.host,self.port_number)
        json = self._submit('get', url)
        _LOGGER.debug("MFI %s: %s" % (self.host,json,))
        if not 'status' in json or not json['status'] == 'success' or not 'sensors' in json:
            raise Exception('Could not determine entities for mfi device %s' % (self.host))
        return json['sensors']

    def update(self,port):
        response = None
        try:
            url = '%s://%s:%s/sensors/%i' % ('https' if self.ssl else 'http', self.host,self.port_number, port)
            json = self._submit( 'get', url )
            _LOGGER.debug("MFI %s: %s" % (self.host,json,))
            # check port
            if not json['sensors'][-1]['port'] == port:
                self.data = None
                raise Exception('Could not find port %i' % (port))
            self.data = json['sensors'][-1]
        except Exception as e:
            _LOGGER.error("Error fetching data from %s: %s" % (self.host,e))
            self.data = None
        return self.data
            
    def set_state(self, port, state):
        url = 'https://%s/sensors/%i' % (self.host,port)
        self._submit( 'put', url, parse_response_as_json=False, data=[('output', state),] )

    def turn_on(self, port):
        return self.set_state( port, 1 )
    def turn_off(self, port):
        return self.set_state( port, 0 )
        

class MfiDeviceSensor(SwitchDevice):
    """Representation of a mFi device sensor."""

    def __init__(self, hass, api, port, name, unit_of_measurement=None):
        """Initialize the REST sensor."""
        self._hass = hass
        self._api = api
        self._port = port
        self._name = name
        self._unit_of_measurement = unit_of_measurement

        self._state = STATE_UNKNOWN
        self._current_power_w = STATE_UNKNOWN
        self._current_voltage_v = STATE_UNKNOWN
        self._current_current_a = STATE_UNKNOWN
        self._today_energy_kwh = STATE_UNKNOWN

        self._api.update(self._port)

    def _update_callback(self, _device, _type, _params):
        """Update the state by the Wemo device."""
        _LOGGER.info("Subscription update for  %s (%s, %s)", _device, _type, _params)
        self._api.update(self._port)
        if not hasattr(self, 'hass'):
            return
        self.schedule_update_ha_state()

    @property
    def should_poll(self):
        return True

    @property
    def force_update(self):
        return True

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def icon(self):
        """Return the icon to use in the frontend, if any."""
        if self._state == STATE_ON:
            return 'mdi:power-plug'
        return 'mdi:power-socket'
        
    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def is_on(self):
        """Return true if switch is on. Standby is on."""
        return self._state


    @property
    def current_power_w(self):
        """Return the current power usage in W."""
        return self._current_power_w

    @property
    def current_voltage_v(self):
        """Return the current voltage usage in v."""
        return self._current_voltage_v

    @property
    def current_current_a(self):
        """Return the current current usage in amperes."""
        return self._current_current_a

    # @property
    # def today_energy_kwh(self):
    #     """Return the today total energy usage in kWh."""
    #     return self._today_energy_kwh

    def turn_on(self, **kwargs):
        """Turn the switch on."""
        self._api.turn_on(self._port)
        self._state = STATE_ON
        self.schedule_update_ha_state()

    def turn_off(self, **kwargs):
        """Turn the switch off."""
        self._api.turn_off(self._port)
        self._state = STATE_OFF
        self.schedule_update_ha_state()


    @property
    def unit_of_measurement(self):
        return 'Watts'
    #     """Return the unit of measurement of this entity, if any."""
    #     try:
    #         tag = self._port.tag
    #     except ValueError:
    #         return 'State'
    #
    #     if tag == 'temperature':
    #         return TEMP_CELSIUS
    #     elif tag == 'active_pwr':
    #         return 'Watts'
    #     elif self._port.model == 'Input Digital':
    #         return 'State'
    #     return tag

    def parse_data(self,data):
        try:
            
            self._state = STATE_ON if data['output'] > 0 else STATE_OFF 
            self._current_power_w = data['power'] if 'power' in data else None
            self._current_voltage_v = data['voltage'] if 'voltage' in data else None
            self._current_current_a = data['current'] if 'current' in data else None
            # self._today_energy_kwh = d['power'] if 'power' in d else None

        except:
            _LOGGER.warn('could not parse state info: %s' % data)
            
        
    def update(self):
        """Get the latest data."""
        data = self._api.update( self._port )
        self.parse_data(data)