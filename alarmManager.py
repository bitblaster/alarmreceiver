#!/usr/bin/python
# -*- coding: utf-8 -*-

import sys
import logging
import re
import time
import paho.mqtt.client as mqtt


DISINSERIMENTO       = 0
INSERIMENTO_TOTALE   = 1
INSERIMENTO_PARZIALE = 2

# ---------------------------------------------------------------------------
# Topic MQTT pubblicati verso Home Assistant
# ---------------------------------------------------------------------------
TOPIC_STATE      = "alarm/state"         # armed_away | armed_home | disarmed | triggered
TOPIC_POWER      = "alarm/fault/power"   # ON / OFF
TOPIC_BATTERY    = "alarm/fault/battery" # ON / OFF
TOPIC_DEVICE     = "alarm/fault/device"  # ON / OFF
TOPIC_OUTPUT     = "alarm/fault/output"  # ON / OFF
TOPIC_BYPASS     = "alarm/fault/bypass"  # ON / OFF
TOPIC_LAST_EVENT = "alarm/last_event"    # testo libero dell'ultimo evento


class Config:
    config = None

    @staticmethod
    def load(configFile):
        import configparser
        Config.config = configparser.ConfigParser()
        print('Loading config file ' + configFile)
        Config.config.read_file(open(configFile))

    @staticmethod
    def get(key, defaultValue=None):
        if not Config.config:
            raise Exception('No config file loaded!')
        try:
            return Config.config.get('DEFAULT', key)
        except Exception as e:
            if defaultValue is None:
                raise e
            return defaultValue

    @staticmethod
    def getInt(key, defaultValue=None):
        try:
            return int(Config.get(key))
        except Exception as e:
            if defaultValue is None:
                raise e
            return defaultValue

    @staticmethod
    def getArray(key, splitChar=','):
        return [x.strip() for x in Config.get(key, '').split(splitChar)]


class FakeSecHead(object):
    def __init__(self, fp):
        self.fp = fp
        self.sechead = '[DEFAULT]\n'

    def readline(self):
        if self.sechead:
            try:
                return self.sechead
            finally:
                self.sechead = None
        else:
            return self.fp.readline()


# ---------------------------------------------------------------------------
# Mappatura codici eventi SIA-IP → topic MQTT
#
# CL → Inserimento totale         → alarm/state = armed_away
# NL → Inserimento parziale       → alarm/state = armed_home
# OP → Disinserimento             → alarm/state = disarmed
# BA → Allarme intrusione         → alarm/state = triggered
# TA → Sabotaggio                 → alarm/state = triggered
# BR → Ripristino allarme         → alarm/state = (stato precedente)
# TR → Ripristino sabotaggio      → alarm/state = (stato precedente)
# AT → Mancanza alimentazione     → alarm/fault/power   = ON
# AR → Ripristino alimentazione   → alarm/fault/power   = OFF
# YM → Corto/disconn. batteria    → alarm/fault/battery = ON
# YT → Batteria inefficiente      → alarm/fault/battery = ON
# YR → Ripristino batteria        → alarm/fault/battery = OFF
# EM → Scomparsa dispositivo      → alarm/fault/device  = ON
# EN → Ripristino dispositivo     → alarm/fault/device  = OFF
# BB → Esclusione zona            → alarm/fault/bypass  = ON
# BU → Ripristino esclusione      → alarm/fault/bypass  = OFF
# OU → Malfunzionamento uscita    → alarm/fault/output  = ON
# OV → Ripristino uscita          → alarm/fault/output  = OFF
# BC/JP/DD/LB/LX → solo last_event (nessun binary sensor)
# ---------------------------------------------------------------------------

class AlarmManager:
    alarmPattern = re.compile(r"\[#[0-9]{6}\|....(..)([0-9]+)\^?([^\^]*)\^?\]")

    def __init__(self):
        self.alarmActive = DISINSERIMENTO
        self.reactions   = {
            "UX": {"subject": "Non definito",                           "execute": None},

            # Stato allarme
            "BA": {"subject": "ALLARME INTRUSIONE",                    "execute": self.allarmeIntrusione},
            "TA": {"subject": "SABOTAGGIO",                            "execute": self.sabotaggio},
            "CL": {"subject": "Inserimento totale",                    "execute": self.inserimentoTotale},
            "NL": {"subject": "Inserimento parziale",                  "execute": self.inserimentoParziale},
            "OP": {"subject": "Disinserimento",                        "execute": self.disinserimento},
            "BR": {"subject": "Ripristino allarme intrusione",         "execute": self.ripristinoAllarme},
            "TR": {"subject": "Ripristino sabotaggio",                 "execute": self.ripristinoAllarme},

            # Alimentazione
            "AT": {"subject": "Mancanza alimentazione",                "execute": self.faultPowerOn},
            "AR": {"subject": "Ripristino alimentazione",              "execute": self.faultPowerOff},

            # Batteria
            "YM": {"subject": "Corto circuito/disconnessione batteria","execute": self.faultBatteryOn},
            "YT": {"subject": "Batteria inefficiente",                 "execute": self.faultBatteryOn},
            "YR": {"subject": "Ripristino batteria",                   "execute": self.faultBatteryOff},
            "XT": {"subject": "Resistenza interna batteria",           "execute": self.onlyLastEvent},
            "XR": {"subject": "Ripristino resistenza interna batteria","execute": self.onlyLastEvent},

            # Dispositivi
            "EM": {"subject": "Scomparsa dispositivo",                 "execute": self.faultDeviceOn},
            "EN": {"subject": "Ripristino scomparsa dispositivo",      "execute": self.faultDeviceOff},

            # Esclusioni
            "BB": {"subject": "Esclusione zona",                       "execute": self.faultBypassOn},
            "BU": {"subject": "Ripristino esclusione",                 "execute": self.faultBypassOff},

            # Uscite
            "OU": {"subject": "Malfunzionamento uscita",               "execute": self.faultOutputOn},
            "OV": {"subject": "Ripristino malfunzionamento uscita",    "execute": self.faultOutputOff},

            # Solo informativi
            "BC": {"subject": "Reset memoria",                         "execute": self.onlyLastEvent},
            "JP": {"subject": "Riconoscimento codice/chiave",          "execute": self.onlyLastEvent},
            "DD": {"subject": "Codice/chiave errati",                  "execute": self.onlyLastEvent},
            "LB": {"subject": "Ingresso programmazione",               "execute": self.onlyLastEvent},
            "LX": {"subject": "Uscita programmazione",                 "execute": self.onlyLastEvent},
        }

    # -----------------------------------------------------------------------
    # Parsing
    # -----------------------------------------------------------------------

    def manageAlarmMessage(self, msg):
        m = AlarmManager.alarmPattern.search(msg)
        if not m:
            return
        tipo  = m.group(1)
        param = m.group(2)
        desc  = re.sub(r'\s\s+', ' ', m.group(3)).strip()
        logging.info(f"Tipo evento: {tipo}, param: {param}, testo: {desc}")

        if tipo not in self.reactions:
            logging.warning(f"Evento sconosciuto: {tipo}: {desc}")
            return

        reaction      = self.reactions[tipo]
        subject       = reaction["subject"]
        message       = subject + (f": {desc}" if desc else "")
        executeMethod = reaction["execute"]

        # Pubblica sempre l'ultimo evento in chiaro
        self.mqttPublish(TOPIC_LAST_EVENT, message)

        if executeMethod:
            try:
                executeMethod(subject, message, param)
            except Exception as e:
                logging.error(f"Error executing handler for {tipo}: {e}")

    # -----------------------------------------------------------------------
    # Handler stato allarme
    # -----------------------------------------------------------------------

    def inserimentoTotale(self, subject, message, param):
        self.alarmActive = INSERIMENTO_TOTALE
        self.mqttPublish(TOPIC_STATE, "armed_away")

    def inserimentoParziale(self, subject, message, param):
        self.alarmActive = INSERIMENTO_PARZIALE
        self.mqttPublish(TOPIC_STATE, "armed_home")

    def disinserimento(self, subject, message, param):
        self.alarmActive = DISINSERIMENTO
        self.mqttPublish(TOPIC_STATE, "disarmed")

    def allarmeIntrusione(self, subject, message, param):
        self.mqttPublish(TOPIC_STATE, "triggered")

    def sabotaggio(self, subject, message, param):
        self.mqttPublish(TOPIC_STATE, "triggered")

    def ripristinoAllarme(self, subject, message, param):
        if self.alarmActive == INSERIMENTO_TOTALE:
            self.mqttPublish(TOPIC_STATE, "armed_away")
        elif self.alarmActive == INSERIMENTO_PARZIALE:
            self.mqttPublish(TOPIC_STATE, "armed_home")
        else:
            self.mqttPublish(TOPIC_STATE, "disarmed")

    # -----------------------------------------------------------------------
    # Handler fault binari
    # -----------------------------------------------------------------------

    def faultPowerOn(self, subject, message, param):
        self.mqttPublish(TOPIC_POWER, "ON")

    def faultPowerOff(self, subject, message, param):
        self.mqttPublish(TOPIC_POWER, "OFF")

    def faultBatteryOn(self, subject, message, param):
        self.mqttPublish(TOPIC_BATTERY, "ON")

    def faultBatteryOff(self, subject, message, param):
        self.mqttPublish(TOPIC_BATTERY, "OFF")

    def faultDeviceOn(self, subject, message, param):
        self.mqttPublish(TOPIC_DEVICE, "ON")

    def faultDeviceOff(self, subject, message, param):
        self.mqttPublish(TOPIC_DEVICE, "OFF")

    def faultBypassOn(self, subject, message, param):
        self.mqttPublish(TOPIC_BYPASS, "ON")

    def faultBypassOff(self, subject, message, param):
        self.mqttPublish(TOPIC_BYPASS, "OFF")

    def faultOutputOn(self, subject, message, param):
        self.mqttPublish(TOPIC_OUTPUT, "ON")

    def faultOutputOff(self, subject, message, param):
        self.mqttPublish(TOPIC_OUTPUT, "OFF")

    # -----------------------------------------------------------------------
    # Solo last_event, nessun binary sensor
    # -----------------------------------------------------------------------

    def onlyLastEvent(self, subject, message, param):
        pass  # mqttPublish(TOPIC_LAST_EVENT) già fatto in manageAlarmMessage

    # -----------------------------------------------------------------------
    # MQTT helper
    # -----------------------------------------------------------------------

    def mqttPublish(self, topic, payload):
        logging.info(f"MQTT publish → {topic} : {payload}")
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            client.username_pw_set(Config.get('mqtt_user'), Config.get('mqtt_password'))
            client.connect(Config.get('mqtt_broker'), Config.getInt('mqtt_port'), 60)
            client.publish(topic, payload, retain=True)
            client.disconnect()
        except Exception as e:
            logging.error(f"MQTT publish failed ({topic}): {e}")

    # Alias per compatibilità con eventuale codice esterno
    def updateHAStatus(self, stato):
        mapping = {
            "INSERIMENTO_TOTALE":   "armed_away",
            "INSERIMENTO_PARZIALE": "armed_home",
            "DISINSERIMENTO":       "disarmed",
        }
        self.mqttPublish(TOPIC_STATE, mapping.get(stato, stato))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logging.info('-------- AlarmManager TEST startup --------')
    Config.load('/etc/alarmReceiver.conf')

    alarmManager = AlarmManager()
    # Test disinserimento
    s = '"SIA-DCS"0714L0#001234[#001234|Nri0OP4^Sirena Interna  ^]_11:47:38,11-13-2025'
    alarmManager.manageAlarmMessage(s)
