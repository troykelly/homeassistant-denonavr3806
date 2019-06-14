"""
Support for Denon AVR 3806 with IP to Serial.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/media_player.denonavr3806/
"""
import logging
import asyncio
import time

import voluptuous as vol

from homeassistant.components.media_player import (
    PLATFORM_SCHEMA, MediaPlayerDevice
)

from homeassistant.components.media_player.const import (
    SUPPORT_NEXT_TRACK, SUPPORT_PAUSE, SUPPORT_PLAY,
    SUPPORT_PREVIOUS_TRACK, SUPPORT_SELECT_SOURCE, SUPPORT_STOP,
    SUPPORT_TURN_OFF, SUPPORT_TURN_ON, SUPPORT_VOLUME_MUTE, SUPPORT_VOLUME_SET
    )
from homeassistant.const import (
    CONF_HOST, CONF_PORT, CONF_NAME, STATE_OFF, STATE_ON, STATE_UNKNOWN)
import homeassistant.helpers.config_validation as cv

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = 'AVR3806'

SUPPORT_DENON = SUPPORT_VOLUME_SET | SUPPORT_VOLUME_MUTE | \
    SUPPORT_TURN_ON | SUPPORT_TURN_OFF | SUPPORT_SELECT_SOURCE \

SUPPORT_MEDIA_MODES = SUPPORT_PAUSE | SUPPORT_STOP | \
    SUPPORT_PREVIOUS_TRACK | SUPPORT_NEXT_TRACK | SUPPORT_PLAY

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_HOST): cv.string,
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Optional(CONF_PORT, default=23): cv.positive_int,
})

NORMAL_INPUTS = {"Phono": "PHONO",
                 "CD": "CD",
                 "DVD": "DVD",
                 "VDP": "VDP",
                 "TV": "TV",
                 "Satellite": "DBS",
                 "VCR-1": "VCR-1",
                 "VCR-2": "VCR-2",
                 "VCR-3": "VCR-3",
                 "Auxiliary Video": "V.AUX",
                 "Tape": "CDR/TAPE"}

MEDIA_MODES = {'Tuner': 'TUNER'}

MESSAGE_QUEUE = []
MESSAGE_DELAY = 200
LAST_MESSAGE = 0


def current_milli_time(): return int(round(time.time() * 1000))


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the Denon platform."""
    _LOGGER.debug("Connecting to %s:%d" %
                  (config.get(CONF_HOST), config.get(CONF_PORT)))
    try:
        reader, writer = await asyncio.open_connection(config.get(CONF_HOST), config.get(CONF_PORT))
    except OSError:
        return False

    _LOGGER.debug("Connected to %s:%d" %
                  (config.get(CONF_HOST), config.get(CONF_PORT)))

    async def inboundHandler(zone1, zone2, zone3):
        while True:
            # raw_data = await reader.read(100)
            raw_data = await reader.readuntil(b'\r')
            if raw_data:
                data = raw_data.decode('ASCII')
                data = data[:-1]
                _LOGGER.debug("Received: %s" % data)
                hass.loop.call_soon(zone1, data)
                hass.loop.call_soon(zone2, data)
                hass.loop.call_soon(zone3, data)

    def outboundHandler(new_message=None):
        global MESSAGE_QUEUE
        global MESSAGE_DELAY
        global LAST_MESSAGE
        if new_message is not None:
            MESSAGE_QUEUE.insert(
                0, {"ts": current_milli_time(), "data": new_message})
        if not MESSAGE_QUEUE:
            return True
        not_before = LAST_MESSAGE + MESSAGE_DELAY
        if not_before > current_milli_time():
            delay = (not_before - current_milli_time()) / 1000
            return hass.loop.call_later(delay, outboundHandler)
        message = MESSAGE_QUEUE.pop()
        LAST_MESSAGE = current_milli_time()
        bytes = bytearray(message["data"] + "\r", 'ASCII')
        writer.write(bytes)
        # writer.drain()
        _LOGGER.debug("Sent: %s" % message["data"])
        return True

    denonZ1 = DenonDevice(hass, 1, config.get(CONF_NAME), outboundHandler)
    denonZ2 = DenonDevice(hass, 2, config.get(CONF_NAME), outboundHandler)
    denonZ3 = DenonDevice(hass, 3, config.get(CONF_NAME), outboundHandler)

    asyncio.ensure_future(inboundHandler(
        denonZ1.inbound_data, denonZ2.inbound_data, denonZ3.inbound_data))

    if await denonZ1.async_update():
        async_add_entities([denonZ1])

    if await denonZ2.async_update():
        async_add_entities([denonZ2])

    if await denonZ3.async_update():
        async_add_entities([denonZ3])

    return True


class DenonDevice(MediaPlayerDevice):
    """Representation of a Denon device."""

    def __init__(self, hass, zone, name, writer):
        """Initialize the Denon device."""
        self._zone = "M" if zone == 1 else str(zone)
        self._hass = hass
        self._name = name if self._zone == "M" else name + \
            ' Zone ' + str(self._zone)
        self._state = STATE_UNKNOWN
        self._volume = 0
        # Initial value 60dB, changed if we get a MVMAX
        self._volume_max = 98
        self._source_list = NORMAL_INPUTS.copy()
        self._source_list.update(MEDIA_MODES)
        if self._zone != "M":
            self._source_list.update({'Zone 1': 'SOURCE'})
        self._muted = False
        self._mediasource = ''
        self._mediainfo = ''

        self._writer = writer

        self._should_setup_sources = True

    def _process_inbound(self, command):
        if command == 'PWOFF':
            self._state = STATE_OFF
        elif command.startswith('MVMAX'):
            self._volume_max = int(command[-2:])
        elif command.startswith('Z' + self._zone):
            data = command[2:]
            if data == 'OFF':
                self._state = STATE_OFF
            elif data == 'ON':
                self._state = STATE_ON
            elif data.startswith('MU'):
                self._muted = True if data[-2:] == 'ON' else False
            elif data in self._source_list.values():
                self._mediasource = data
            elif data.isdigit():
                volume = int(data)
                if volume > self._volume_max:
                    self._volume = 0
                else:
                    self._volume = volume / self._volume_max
        elif self._zone == "M":
            if command.startswith("SI") and command[2:] in self._source_list.values():
                self._mediasource = command[2:]
            elif command.startswith("MU"):
                self._muted = True if command[-2:] == 'ON' else False
            elif command.startswith("MV"):
                volume = int(command[-2:])
                if volume > self._volume_max:
                    self._volume = 0
                else:
                    self._volume = volume / self._volume_max

    def inbound_data(self, command):
        self._process_inbound(command)
        return True

    async def async_inbound_data(self, command):
        self._process_inbound(command)
        return True

    def _write(self, data):
        self._hass.loop.call_soon(self._writer, data)

    def _setup_sources(self):
        # NSFRN - Network name
        if self._zone == "M":
            self._write("NSFRN ?")

        # SSFUN - Configured sources with names
        if self._zone == "M":
            self._write("SSFUN ?")

        # SSSOD - Deleted sources
        if self._zone == "M":
            self._write("SSSOD ?")

    async def async_update(self):
        """Get the latest details from the device."""

        if self._should_setup_sources:
            self._setup_sources()
            self._should_setup_sources = False

        if self._zone == "M":
            self._write("PW?")
            self._write("SI?")
            self._write("MV?")
            self._write("CV?")
            self._write("MU?")
        else:
            self._write("Z" + self._zone + "MU?")
        # Check Zone
        self._write("Z" + self._zone + "?")

        return True

    @property
    def name(self):
        """Return the name of the device."""
        return self._name

    @property
    def state(self):
        """Return the state of the device."""
        return self._state if self._state else STATE_UNKNOWN

    @property
    def volume_level(self):
        """Volume level of the media player (0..1)."""
        return self._volume

    @property
    def is_volume_muted(self):
        """Return boolean if volume is currently muted."""
        return self._muted

    @property
    def source_list(self):
        """Return the list of available input sources."""
        return sorted(list(self._source_list.keys()))

    @property
    def media_title(self):
        """Return the current media info."""
        return self._mediainfo

    @property
    def supported_features(self):
        """Flag media player features that are supported."""
        if self._mediasource in MEDIA_MODES.values():
            return SUPPORT_DENON | SUPPORT_MEDIA_MODES
        return SUPPORT_DENON

    @property
    def source(self):
        """Return the current input source."""
        for pretty_name, name in self._source_list.items():
            if self._mediasource == name:
                return pretty_name

    def turn_off(self):
        """Turn off media player."""
        self._write("Z" + self._zone + "OFF")

    def volume_up(self):
        """Volume up media player."""
        self.telnet_command('MVUP')

    def volume_down(self):
        """Volume down media player."""
        self.telnet_command('MVDOWN')

    def set_volume_level(self, volume):
        """Set volume level, range 0..1."""
        if volume == 0:
            set_volume = self._volume_max + 1
        else:
            set_volume = str(round(volume * self._volume_max)).zfill(2)
        if self._zone == 'M':
            self._write("MV" + set_volume)
        else:
            self._write("Z" + self._zone + str(set_volume))

    def mute_volume(self, mute):
        """Mute (true) or unmute (false) media player."""
        if self._zone == 'M':
            self._write("MU" + ('ON' if mute else 'OFF'))
        else:
            self._write("Z" + self._zone + "MU" + ('ON' if mute else 'OFF'))

    def media_play(self):
        """Play media player."""
        self.telnet_command('NS9A')

    def media_pause(self):
        """Pause media player."""
        self.telnet_command('NS9B')

    def media_stop(self):
        """Pause media player."""
        self.telnet_command('NS9C')

    def media_next_track(self):
        """Send the next track command."""
        self.telnet_command('NS9D')

    def media_previous_track(self):
        """Send the previous track command."""
        self.telnet_command('NS9E')

    def turn_on(self):
        """Turn the media player on."""
        self._write("Z" + self._zone + "ON")

    def select_source(self, source):
        """Select input source."""
        if self._zone == 'M':
            self._write('SI' + self._source_list.get(source))
        else:
            self._write("Z" + self._zone + self._source_list.get(source))
